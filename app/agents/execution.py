from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.core.enums import AgentRole
from app.domain.models.order import Order, OrderRequest
from app.domain.models.order_intent import OrderIntent
from app.events.event import OrderIntentCreated, OrderSubmitted

logger = logging.getLogger(__name__)


class ExecutionAgent(BaseAgent):
    """Traduz OrderIntent em OrderRequest e submete à exchange."""

    role = AgentRole.EXECUTION
    name = "execution"

    async def execute(self, intent: OrderIntent) -> Order:
        await self.context.event_bus.publish(
            OrderIntentCreated(
                intent_id=intent.intent_id,
                signal_id=intent.signal_id,
                symbol=intent.symbol,
                side=intent.side.value,
                quantity=float(intent.quantity),
            )
        )

        request = OrderRequest(
            client_order_id=f"ai-{intent.intent_id[:24]}",
            symbol=intent.symbol,
            market_type=intent.market_type,
            side=intent.side,
            type=intent.order_type,
            quantity=intent.quantity,
            price=intent.price,
            stop_price=intent.stop_price,
        )

        order = await self.context.exchange.orders.place_order(request)

        await self.context.event_bus.publish(
            OrderSubmitted(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
            )
        )
        return order