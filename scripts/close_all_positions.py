"""Fecha todas as posições Futures abertas via ordens MARKET reduce-only.

Uso:
    python scripts/close_all_positions.py

Segurança:
    - Só opera em modo demo (`testnet=True` hardcoded).
    - `reduce_only=True` — nunca abre posição nova.
    - Idempotente: rodar 2× não faz nada na segunda vez.

Diferença vs. versão anterior:
    Este script agora publica `OrderFilled` num `EventBus` local, com
    `RoundTripAgent` + `TradeRecorderAgent` ativos. Isso fecha os
    `round_trips` correspondentes em tempo real — sem precisar rodar
    `reconcile_round_trips.py` depois.

    O reconciler continua existindo como rede de segurança para o caso de
    o bot ser interrompido no meio de um close (`kill -9`, crash etc.).
"""

from __future__ import annotations

import asyncio
import logging

from app.agents.round_trip import RoundTripAgent
from app.agents.trade_recorder import TradeRecorderAgent
from app.core.config import settings
from app.core.logging import setup_logging
from app.database.session import AsyncSessionLocal
from app.events.bus import EventBus
from app.events.event import OrderFilled
from app.exchanges.binance.adapter import BinanceAdapter
from app.orchestration.context import AgentContext
from app.orchestration.registry import AgentRegistry


async def _publish_close_fill(
    event_bus: EventBus,
    order,
) -> None:
    """Publica um `OrderFilled` para o `Order` retornado por `close_position`.

    O `Order` já vem com `fills` anexados (via `/fapi/v1/userTrades`),
    então o `RoundTripAgent`/`TradeRecorderAgent` conseguem processar
    sem nenhuma chamada REST adicional.
    """
    if order is None or not order.fills:
        return
    await event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=float(order.executed_quantity),
            average_price=float(order.average_price or 0),
            order=order,
        )
    )


async def main() -> None:
    setup_logging("INFO")
    logger = logging.getLogger("close_positions")

    exchange = BinanceAdapter(testnet=True)
    event_bus = EventBus()
    registry = AgentRegistry()
    context = AgentContext(
        exchange=exchange,
        event_bus=event_bus,
        settings=settings,
        registry=registry,
        session_factory=AsyncSessionLocal,
    )

    # Ordem importa: RoundTripAgent precisa consumir o OrderFilled ANTES
    # do TradeRecorderAgent, para que o `RoundTripAssigned` já esteja em
    # cache quando o recorder for popular `round_trip_id`/`role`.
    round_trip = RoundTripAgent(context)
    trade_recorder = TradeRecorderAgent(context)

    await round_trip.start()
    await trade_recorder.start()

    try:
        positions = await exchange.account.get_futures_positions()
        if not positions:
            print("Nenhuma posição aberta. Nada a fazer.")
            return

        print(f"Encontradas {len(positions)} posições abertas:")
        for p in positions:
            print(
                f"  {p.symbol:12s} {p.position_side.value:5s} "
                f"qty={p.quantity} entry={p.entry_price} "
                f"pnl={p.unrealized_pnl}"
            )
        print()

        for p in positions:
            print(f"Fechando {p.symbol} ({p.position_side.value})...")
            try:
                order = await exchange.positions.close_position(
                    p.symbol, p.position_side
                )
                await _publish_close_fill(event_bus, order)
                print(f"  ✅ {p.symbol} ordem de fechamento enviada")
            except Exception as exc:
                logger.exception(
                    "close.failed", extra={"symbol": p.symbol, "error": str(exc)}
                )
                print(f"  ❌ {p.symbol} falhou: {exc}")

        # Aguarda propagação dos eventos (RoundTripAgent fecha trips,
        # TradeRecorderAgent persiste trades) + verificação da exchange.
        await asyncio.sleep(3)

        remaining = await exchange.account.get_futures_positions()
        print()
        print(f"Posições abertas após fechamento: {len(remaining)}")
        for p in remaining:
            print(f"  {p.symbol} {p.position_side.value} qty={p.quantity}")
    finally:
        await trade_recorder.stop()
        await round_trip.stop()
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())