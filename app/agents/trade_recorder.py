"""Persiste fills de ordens como `TradeORM`.

Ouve `OrderFilled` (publicado pelo `ExecutionAgent` ou pelo UDS) e escreve
uma linha em `trades` por fill. Idempotente via `trade_id`.

Também liga cada fill ao ciclo correspondente (`round_trip_id` + `role`),
consumindo `RoundTripAssigned` (publicado pelo `RoundTripAgent`) num cache
em memória `{order_id: (round_trip_id, role)}`.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole, TradeRole
from app.database.models.trade import TradeORM
from app.domain.models.order import Order, OrderFill
from app.events.event import Event, OrderFilled, RoundTripAssigned

logger = logging.getLogger(__name__)


class TradeRecorderAgent(BaseAgent):
    """Persiste cada `OrderFill` como uma linha em `trades`."""

    role = AgentRole.AUDITOR
    name = "trade_recorder"

    def __init__(self, context) -> None:
        super().__init__(context)
        # order_id -> (round_trip_id | None, role)
        self._assignments: dict[str, tuple[str | None, str]] = {}

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {
            OrderFilled: self._on_order_filled,
            RoundTripAssigned: self._on_round_trip_assigned,
        }

    @staticmethod
    def _trade_id(order: Order, fill: OrderFill, index: int) -> str:
        if fill.trade_id:
            return f"{order.exchange_order_id}-{fill.trade_id}"
        return f"{order.exchange_order_id}-{index}"

    async def _on_round_trip_assigned(self, event: Event) -> None:
        if not isinstance(event, RoundTripAssigned):
            return
        self._assignments[event.order_id] = (event.round_trip_id, event.role)

    async def _on_order_filled(self, event: Event) -> None:
        if not isinstance(event, OrderFilled):
            return

        order = event.order
        if order is None:
            logger.debug(
                "trade_recorder.skip_no_order_payload",
                extra={"order_id": event.exchange_order_id},
            )
            return

        if self.context.session_factory is None:
            logger.debug(
                "trade_recorder.skip_no_session_factory",
                extra={"order_id": order.exchange_order_id},
            )
            return

        if not order.fills:
            logger.debug(
                "trade_recorder.skip_no_fills",
                extra={"order_id": order.exchange_order_id},
            )
            return

        round_trip_id, role = self._assignments.pop(
            order.exchange_order_id, (None, TradeRole.UNKNOWN.value)
        )

        rows = [
            TradeORM(
                trade_id=self._trade_id(order, fill, i),
                order_id=order.exchange_order_id,
                symbol=order.symbol,
                market_type=order.market_type.value,
                side=order.side.value,
                position_side=(
                    order.position_side.value if order.position_side else None
                ),
                quantity=fill.quantity,
                price=fill.price,
                fee=fill.commission,
                fee_asset=fill.commission_asset,
                realized_pnl=None,
                executed_at=fill.timestamp,
                round_trip_id=round_trip_id,
                role=role,
            )
            for i, fill in enumerate(order.fills)
        ]

        async with self.context.session_factory() as session:
            for row in rows:
                session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.debug(
                    "trade_recorder.duplicate_skipped",
                    extra={
                        "order_id": order.exchange_order_id,
                        "num_fills": len(rows),
                    },
                )
                return

        logger.info(
            "trade_recorder.persisted",
            extra={
                "order_id": order.exchange_order_id,
                "symbol": order.symbol,
                "num_fills": len(rows),
                "round_trip_id": round_trip_id,
                "role": role,
            },
        )