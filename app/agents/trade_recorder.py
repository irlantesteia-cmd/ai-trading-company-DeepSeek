"""Persiste fills de ordens como `TradeORM`.

Ouve `OrderFilled` (publicado pelo `ExecutionAgent` quando o `place_order`
já retorna fills) e escreve uma linha em `trades` por fill. Este é o elo
que fecha o ciclo de dados: sem ele, o `MetricsCollector` lê de um DB
vazio e sempre retorna métricas zeradas.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole
from app.database.models.trade import TradeORM
from app.events.event import Event, OrderFilled

logger = logging.getLogger(__name__)


class TradeRecorderAgent(BaseAgent):
    """Persiste cada `OrderFill` como uma linha em `trades`.

    Idempotente: o `trade_id` é determinístico (`{exchange_order_id}-{i}`).
    Reentrega do mesmo evento gera `IntegrityError` no commit, capturado e
    tratado como duplicata (rollback + log).
    """

    role = AgentRole.AUDITOR
    name = "trade_recorder"

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {OrderFilled: self._on_order_filled}

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

        rows = [
            TradeORM(
                trade_id=f"{order.exchange_order_id}-{i}",
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
                logger.warning(
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
            },
        )