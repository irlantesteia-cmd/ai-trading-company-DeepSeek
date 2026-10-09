"""Tradução de eventos brutos do User Data Stream em efeitos de domínio.

Para cada `ORDER_TRADE_UPDATE`:
- grava o estado da ordem em `orders` (todo tipo de execução: NEW, CANCELED,
  EXPIRED, TRADE, ...), se houver `OrderRecorder`;
- publica `OrderFilled` quando o evento é um fill (comportamento anterior).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.enums import MarketType
from app.events.bus import EventBus
from app.events.event import OrderFilled
from app.exchanges.binance.mappers import map_order_update
from app.runtime.order_recorder import OrderRecorder

logger = logging.getLogger(__name__)


async def handle_user_stream_event(
    event: dict[str, Any],
    *,
    market_type: MarketType,
    event_bus: EventBus,
    recorder: OrderRecorder | None = None,
) -> None:
    if event.get("e") != "ORDER_TRADE_UPDATE":
        return
    order = map_order_update(event, market_type)

    if recorder is not None:
        await recorder.record(order)

    if not order.fills or order.average_price is None:
        return
    await event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=float(order.executed_quantity),
            average_price=float(order.average_price),
            order=order,
        )
    )
    logger.info(
        "user_stream.order_filled",
        extra={
            "order_id": order.exchange_order_id,
            "symbol": order.symbol,
            "status": order.status.value,
            "num_fills": len(order.fills),
        },
    )
