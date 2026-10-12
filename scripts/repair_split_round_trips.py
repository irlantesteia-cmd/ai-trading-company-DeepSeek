r"""Repara ciclos (`round_trips`) partidos pelo bug de fills contados em dobro.

Sintoma (ao vivo, 2026-10-12): uma ordem de fechamento executada em vários
fills chegou pelo User Data Stream e pelo REST ao mesmo tempo; o ciclo
original fechou antes da hora e os últimos fills da MESMA ordem abriram um
ciclo "fantasma" do lado oposto, OPEN no banco e inexistente na corretora.
Uma entrada real posterior no mesmo símbolo e lado foi somada ao fantasma (e
a saída por tempo a fechou em 1 minuto, pela idade do fantasma).

Para cada ciclo OPEN que contém fills de uma ordem cujos outros fills são
saída de um ciclo CLOSED do mesmo símbolo e lado oposto:
- esses fills voltam para o ciclo original como EXIT e o original é
  recalculado a partir de `trades` (fonte da verdade);
- os demais fills do fantasma (operações reais posteriores) viram um ciclo
  próprio, recalculado do mesmo jeito;
- o fantasma vira `VOID` (não é apagado; fica fora de todas as contas).

Nada muda sem `--apply`. Com posição aberta na corretora no símbolo, o
ciclo é pulado (o estado ainda está mudando).

Uso (PowerShell):
    .\.venv\Scripts\python scripts/repair_split_round_trips.py           # só mostra
    .\.venv\Scripts\python scripts/repair_split_round_trips.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.core.enums import CloseReason, PositionSide, RoundTripStatus, TradeRole
from app.database.models.order import OrderORM
from app.database.models.round_trip import RoundTripORM
from app.database.models.trade import TradeORM
from app.database.session import AsyncSessionLocal


@dataclass
class TripValues:
    """Valores de um ciclo recalculados a partir dos seus trades."""

    entry_quantity: Decimal
    entry_avg_price: Decimal
    entry_fee: Decimal
    exit_quantity: Decimal
    exit_avg_price: Decimal | None
    exit_fee: Decimal
    gross_pnl: Decimal | None
    net_pnl: Decimal | None
    closed: bool


@dataclass
class Repair:
    phantom: RoundTripORM
    original: RoundTripORM
    back_to_original: list[TradeORM]
    original_values: TripValues
    new_trip_trades: list[TradeORM] = field(default_factory=list)
    new_trip_values: TripValues | None = None
    new_trip_reason: str | None = None


def _vwap(trades: list[TradeORM]) -> tuple[Decimal, Decimal | None, Decimal]:
    qty = sum((t.quantity for t in trades), Decimal(0))
    avg = sum((t.quantity * t.price for t in trades), Decimal(0)) / qty if qty > 0 else None
    return qty, avg, sum((t.fee for t in trades), Decimal(0))


def trip_values(position_side: str, entries: list[TradeORM], exits: list[TradeORM]) -> TripValues:
    entry_qty, entry_avg, entry_fee = _vwap(entries)
    exit_qty, exit_avg, exit_fee = _vwap(exits)
    closed = exit_qty > 0 and exit_qty >= entry_qty
    gross = net = None
    if closed and entry_avg is not None and exit_avg is not None:
        qty = min(entry_qty, exit_qty)
        sign = 1 if position_side == PositionSide.LONG.value else -1
        gross = (exit_avg - entry_avg) * qty * sign
        net = gross - entry_fee - exit_fee
    return TripValues(entry_qty, entry_avg or Decimal(0), entry_fee, exit_qty, exit_avg,
                      exit_fee, gross, net, closed)


def _reason(client_order_id: str | None) -> str:
    return CloseReason.from_client_order_id(client_order_id or "").value


def _entry_side(position_side: str) -> str:
    return "BUY" if position_side == PositionSide.LONG.value else "SELL"


def plan_repairs(
    trips: list[RoundTripORM],
    trades: list[TradeORM],
    client_ids: dict[str, str] | None = None,
) -> list[Repair]:
    """Função pura: encontra os fantasmas e calcula como devem ficar."""
    client_ids = client_ids or {}
    by_trip: dict[str, list[TradeORM]] = {}
    for t in trades:
        if t.round_trip_id:
            by_trip.setdefault(t.round_trip_id, []).append(t)
    trips_by_id = {t.round_trip_id: t for t in trips}
    repairs: list[Repair] = []
    for phantom in trips:
        if phantom.status != RoundTripStatus.OPEN.value:
            continue
        own = by_trip.get(phantom.round_trip_id, [])
        for order_id in sorted({t.order_id for t in own}):
            others = {
                t.round_trip_id for t in trades
                if t.order_id == order_id and t.role == TradeRole.EXIT.value
                and t.round_trip_id not in (None, phantom.round_trip_id)
            }
            if len(others) != 1:
                continue
            original = trips_by_id.get(others.pop() or "")
            if (
                original is None
                or original.status != RoundTripStatus.CLOSED.value
                or original.symbol != phantom.symbol
                or original.position_side == phantom.position_side
            ):
                continue
            back = [t for t in own if t.order_id == order_id]
            original_trades = by_trip.get(original.round_trip_id, [])
            values = trip_values(
                original.position_side,
                [t for t in original_trades if t.role == TradeRole.ENTRY.value],
                [t for t in original_trades if t.role == TradeRole.EXIT.value] + back,
            )
            repair = Repair(phantom, original, back, values)
            rest = sorted((t for t in own if t.order_id != order_id), key=lambda t: t.executed_at)
            if rest:
                side = _entry_side(phantom.position_side)
                exits = [t for t in rest if t.side != side]
                repair.new_trip_trades = rest
                repair.new_trip_values = trip_values(
                    phantom.position_side, [t for t in rest if t.side == side], exits
                )
                repair.new_trip_reason = _reason(client_ids.get(exits[-1].order_id)) if exits else None
            repairs.append(repair)
            break
    return repairs


def _fmt(v: TripValues) -> str:
    pnl = f"{v.net_pnl:.4f}" if v.net_pnl is not None else "-"
    return (f"entrada {v.entry_quantity} @ {v.entry_avg_price:.2f} | saída {v.exit_quantity}"
            f" | {'FECHADO' if v.closed else 'ABERTO'} | líquido {pnl}")


def _apply_values(trip: RoundTripORM, v: TripValues) -> None:
    trip.entry_quantity = v.entry_quantity
    trip.entry_avg_price = v.entry_avg_price
    trip.entry_fee = v.entry_fee
    trip.exit_quantity = v.exit_quantity
    trip.exit_avg_price = v.exit_avg_price
    trip.exit_fee = v.exit_fee
    trip.gross_pnl = v.gross_pnl
    trip.net_pnl = v.net_pnl


async def _open_position_symbols() -> set[str]:
    from app.exchanges.binance.adapter import BinanceAdapter

    exchange = BinanceAdapter(testnet=True)
    try:
        positions = await exchange.account.get_futures_positions()
    finally:
        close = getattr(exchange, "aclose", None) or getattr(exchange, "close", None)
        if close is not None:
            await close()
    return {p.symbol for p in positions if p.is_open}


async def main(apply: bool) -> int:
    open_symbols = await _open_position_symbols()
    async with AsyncSessionLocal() as session:
        trips = list((await session.execute(select(RoundTripORM))).scalars().all())
        trades = list((await session.execute(select(TradeORM))).scalars().all())
        client_ids = {
            o.exchange_order_id: o.client_order_id
            for o in (await session.execute(select(OrderORM))).scalars().all()
        }
        repairs = plan_repairs(trips, trades, client_ids)
        if not repairs:
            print("Nenhum ciclo partido encontrado.")
            return 0
        for r in repairs:
            print(f"\n{r.phantom.symbol}: fantasma {r.phantom.round_trip_id} ({r.phantom.position_side})")
            print(f"  original {r.original.round_trip_id} ({r.original.position_side}) "
                  f"recebe {len(r.back_to_original)} fills de saída")
            print(f"    antes:  saída {r.original.exit_quantity} | líquido {r.original.net_pnl}")
            print(f"    depois: {_fmt(r.original_values)}")
            if r.new_trip_values is not None:
                print(f"  ciclo novo com {len(r.new_trip_trades)} fills (operação real posterior):")
                print(f"    {_fmt(r.new_trip_values)} | motivo {r.new_trip_reason}")
            print("  fantasma -> VOID")
            if r.phantom.symbol in open_symbols:
                print("  PULADO: há posição aberta na corretora neste símbolo.")
                continue
            if not apply:
                continue
            for t in r.back_to_original:
                t.round_trip_id = r.original.round_trip_id
                t.role = TradeRole.EXIT.value
            _apply_values(r.original, r.original_values)
            v = r.new_trip_values
            if v is not None:
                side = _entry_side(r.phantom.position_side)
                new = RoundTripORM(
                    round_trip_id=str(uuid4()),
                    symbol=r.phantom.symbol,
                    market_type=r.phantom.market_type,
                    position_side=r.phantom.position_side,
                    status=RoundTripStatus.CLOSED.value if v.closed else RoundTripStatus.OPEN.value,
                    opened_at=r.new_trip_trades[0].executed_at,
                    closed_at=r.new_trip_trades[-1].executed_at if v.closed else None,
                    close_reason=r.new_trip_reason if v.closed else None,
                )
                _apply_values(new, v)
                session.add(new)
                for t in r.new_trip_trades:
                    t.round_trip_id = new.round_trip_id
                    t.role = TradeRole.ENTRY.value if t.side == side else TradeRole.EXIT.value
            r.phantom.status = RoundTripStatus.VOID.value
        if apply:
            await session.commit()
            print("\nAplicado.")
        else:
            print("\nNada foi alterado. Rode com --apply para gravar.")
    return len(repairs)


if __name__ == "__main__":
    logging.disable(logging.INFO)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--apply", action="store_true", help="grava as correções")
    asyncio.run(main(p.parse_args().apply))
