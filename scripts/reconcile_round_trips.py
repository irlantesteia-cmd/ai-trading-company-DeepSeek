"""Reconcilia `round_trips` OPEN contra a exchange.

Uso típico:
    python scripts/reconcile_round_trips.py

Fluxo:
1. Lê todos os `round_trips` com `status='OPEN'` no DB.
2. Para cada um, consulta `GET /fapi/v2/positionRisk?symbol=X` para saber se a
   posição ainda existe.
3. Se ainda existe → skip (nada a fazer; o `Reconciler` ou fechamento normal
   vai capturar quando houver fill).
4. Se não existe mais → lê `GET /fapi/v1/userTrades?symbol=X` do momento de
   abertura até agora, filtra os fills que ainda não estão em `trades`, persiste
   cada um como `TradeORM(role='EXIT', round_trip_id=...)`, e marca o trip como
   `CLOSED` com `close_reason` heurístico (MANUAL/STOP_LOSS/TAKE_PROFIT) e P&L
   computado.

Este script é idempotente: se rodar 2x sem nada a fazer, não faz nada.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.core.enums import (
    CloseReason,
    MarketType,
    PositionSide,
    RoundTripStatus,
    TradeRole,
)
from app.core.logging import setup_logging
from app.database.models.round_trip import RoundTripORM
from app.database.models.trade import TradeORM
from app.database.session import AsyncSessionLocal
from app.exchanges.binance.adapter import BinanceAdapter
from app.exchanges.binance.client import BinanceClient

logger = logging.getLogger("reconcile_round_trips")

_QUANTITY_TOLERANCE = Decimal("0.00000001")


# --------------------------------------------------------------------- helpers
def _dec(v) -> Decimal:
    return Decimal(str(v))


def _reason_from_client_order_id(client_order_id: str) -> CloseReason:
    return CloseReason.from_client_order_id(client_order_id)


def _compute_pnl(
    *,
    position_side: str,
    entry_avg: Decimal,
    exit_avg: Decimal,
    qty: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
) -> tuple[Decimal, Decimal]:
    if position_side == PositionSide.LONG.value:
        gross = (exit_avg - entry_avg) * qty
    else:
        gross = (entry_avg - exit_avg) * qty
    net = gross - entry_fee - exit_fee
    return gross, net


# ------------------------------------------------------------------- exchange
async def _has_open_position(exchange: BinanceAdapter, symbol: str) -> bool:
    try:
        positions = await exchange.account.get_futures_positions()
        for pos in positions:
            if pos.symbol == symbol and pos.is_open:
                return True
        return False
    except Exception:
        logger.exception("has_open_position.failed", extra={"symbol": symbol})
        # Default conservador: assume que ainda existe → não fecha trip
        return True


async def _fetch_recent_user_trades(
    client: BinanceClient,
    *,
    symbol: str,
    start_ms: int,
    limit: int = 1000,
) -> list[dict]:
    raw = await client.request(
        "GET",
        "/fapi/v1/userTrades",
        market_type=MarketType.FUTURES,
        signed=True,
        params={"symbol": symbol, "startTime": start_ms, "limit": limit},
    )
    return raw if isinstance(raw, list) else []


async def _fetch_order_client_id(
    client: BinanceClient,
    *,
    symbol: str,
    order_id: str,
) -> str:
    """`/fapi/v1/userTrades` não devolve `clientOrderId`; buscamos no /order."""
    if not order_id:
        return ""
    try:
        raw = await client.request(
            "GET",
            "/fapi/v1/order",
            market_type=MarketType.FUTURES,
            signed=True,
            params={"symbol": symbol, "orderId": order_id},
        )
    except Exception as exc:
        logger.exception(
            "reconcile_trip.order_lookup_failed",
            extra={
                "symbol": symbol,
                "order_id": order_id,
                "error": str(exc) or type(exc).__name__,
            },
        )
        return ""
    if isinstance(raw, dict):
        return str(raw.get("clientOrderId", ""))
    return ""


# ------------------------------------------------------------------- reconcile
async def _reconcile_trip(
    *,
    trip: RoundTripORM,
    exchange: BinanceAdapter,
    client: BinanceClient,
) -> bool:
    """Retorna True se o trip foi fechado, False se pulado."""

    if await _has_open_position(exchange, trip.symbol):
        logger.info(
            "reconcile_trip.skip_position_still_open",
            extra={"symbol": trip.symbol, "round_trip_id": trip.round_trip_id},
        )
        return False

    opened_ms = int(trip.opened_at.timestamp() * 1000)
    try:
        user_trades = await _fetch_recent_user_trades(
            client, symbol=trip.symbol, start_ms=opened_ms
        )
    except Exception:
        logger.exception(
            "reconcile_trip.user_trades_failed",
            extra={"symbol": trip.symbol, "round_trip_id": trip.round_trip_id},
        )
        return False

    # Filtra fills que ainda não estão em `trades` (dedup por trade_id)
    async with AsyncSessionLocal() as session:
        existing_stmt = select(TradeORM.trade_id).where(TradeORM.symbol == trip.symbol)
        existing = set((await session.execute(existing_stmt)).scalars().all())

    new_fills = [
        t
        for t in user_trades
        if str(t.get("id")) and f"{t['orderId']}-{t['id']}" not in existing
    ]

    if not new_fills:
        logger.warning(
            "reconcile_trip.no_new_fills_but_no_position",
            extra={"symbol": trip.symbol, "round_trip_id": trip.round_trip_id},
        )
        return False

    exit_qty = sum((_dec(t["qty"]) for t in new_fills), Decimal(0))
    exit_notional = sum(
        (_dec(t["qty"]) * _dec(t["price"]) for t in new_fills), Decimal(0)
    )
    exit_fee = sum((_dec(t.get("commission", "0")) for t in new_fills), Decimal(0))
    closed_at = datetime.fromtimestamp(int(new_fills[-1]["time"]) / 1000, tz=UTC)

    # close_reason pelo clientOrderId do último fill.
    # `/fapi/v1/userTrades` NÃO devolve `clientOrderId` — só `orderId`.
    # Buscamos o detalhe da ordem para recuperar o clientOrderId real.
    last_order_id = str(new_fills[-1].get("orderId", ""))
    last_client_order_id = await _fetch_order_client_id(
        client, symbol=trip.symbol, order_id=last_order_id
    )
    close_reason = _reason_from_client_order_id(last_client_order_id)

    async with AsyncSessionLocal() as session:
        # Persiste os EXIT trades
        for t in new_fills:
            session.add(
                TradeORM(
                    trade_id=f"{t['orderId']}-{t['id']}",
                    order_id=str(t["orderId"]),
                    symbol=trip.symbol,
                    market_type=trip.market_type,
                    side=t["side"],
                    position_side=trip.position_side,
                    quantity=_dec(t["qty"]),
                    price=_dec(t["price"]),
                    fee=_dec(t.get("commission", "0")),
                    fee_asset=t.get("commissionAsset", ""),
                    realized_pnl=None,
                    executed_at=datetime.fromtimestamp(
                        int(t["time"]) / 1000, tz=UTC
                    ),
                    round_trip_id=trip.round_trip_id,
                    role=TradeRole.EXIT.value,
                )
            )

        # Atualiza o trip (ler dentro da mesma sessão)
        stmt = select(RoundTripORM).where(
            RoundTripORM.round_trip_id == trip.round_trip_id
        )
        db_trip = (await session.execute(stmt)).scalars().first()
        if db_trip is None:
            logger.error(
                "reconcile_trip.trip_disappeared",
                extra={"round_trip_id": trip.round_trip_id},
            )
            return False

        # Acumula com o que já havia (safe se o exit parcial já registrou algo)
        prev_qty = db_trip.exit_quantity or Decimal(0)
        prev_avg = db_trip.exit_avg_price or Decimal(0)
        prev_fee = db_trip.exit_fee or Decimal(0)
        total_qty = prev_qty + exit_qty
        if total_qty > 0:
            combined_notional = prev_qty * prev_avg + exit_notional
            db_trip.exit_avg_price = combined_notional / total_qty
        db_trip.exit_quantity = total_qty
        db_trip.exit_fee = prev_fee + exit_fee

        if total_qty + _QUANTITY_TOLERANCE >= db_trip.entry_quantity:
            qty = min(db_trip.entry_quantity, total_qty)
            gross, net = _compute_pnl(
                position_side=db_trip.position_side,
                entry_avg=db_trip.entry_avg_price,
                exit_avg=db_trip.exit_avg_price,
                qty=qty,
                entry_fee=db_trip.entry_fee or Decimal(0),
                exit_fee=db_trip.exit_fee or Decimal(0),
            )
            db_trip.status = RoundTripStatus.CLOSED.value
            db_trip.closed_at = closed_at
            db_trip.close_reason = close_reason.value
            db_trip.gross_pnl = gross
            db_trip.net_pnl = net

        await session.commit()

    logger.info(
        "reconcile_trip.closed",
        extra={
            "round_trip_id": trip.round_trip_id,
            "symbol": trip.symbol,
            "close_reason": close_reason.value,
            "client_order_id": last_client_order_id,
            "exit_quantity": str(total_qty),
            "exit_avg_price": str(db_trip.exit_avg_price),
            "net_pnl": str(db_trip.net_pnl) if db_trip.net_pnl is not None else None,
            "num_new_fills": len(new_fills),
        },
    )
    return True


# ----------------------------------------------------------------------- main
async def main() -> None:
    setup_logging("INFO")

    async with AsyncSessionLocal() as session:
        stmt = select(RoundTripORM).where(
            RoundTripORM.status == RoundTripStatus.OPEN.value
        )
        open_trips = list((await session.execute(stmt)).scalars().all())

    if not open_trips:
        print("Nenhum round_trip OPEN. Nada a fazer.")
        return

    print(f"Encontrados {len(open_trips)} round_trips OPEN. Reconciliando...")
    for trip in open_trips:
        print(
            f"  {trip.symbol:12s} {trip.position_side:5s} "
            f"entry_qty={trip.entry_quantity} entry_avg={trip.entry_avg_price}"
        )
    print()

    exchange = BinanceAdapter(testnet=True)
    try:
        client = (
            exchange.client if hasattr(exchange, "client") else exchange._client
        )
        closed = 0
        for trip in open_trips:
            try:
                if await _reconcile_trip(trip=trip, exchange=exchange, client=client):
                    closed += 1
            except Exception:
                logger.exception(
                    "reconcile_trip.failed",
                    extra={
                        "symbol": trip.symbol,
                        "round_trip_id": trip.round_trip_id,
                    },
                )
        print()
        print(f"Trips fechados: {closed}/{len(open_trips)}")
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())