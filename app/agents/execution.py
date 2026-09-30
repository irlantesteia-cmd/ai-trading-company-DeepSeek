from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.core.enums import AgentRole
from app.domain.models.order import Order, OrderRequest
from app.domain.models.order_intent import OrderIntent
from app.events.event import OrderFilled, OrderIntentCreated, OrderSubmitted

logger = logging.getLogger(__name__)


class ExecutionAgent(BaseAgent):
    """Traduz OrderIntent em OrderRequest e submete à exchange.

    Após `place_order`, se a ordem retornada já trouxer fills (caso típico
    de MARKET orders com `newOrderRespType=FULL`), publica `OrderFilled`
    com o objeto `Order` completo — o `TradeRecorderAgent` persiste cada
    fill como `TradeORM`.

    Ordens que preenchem assincronamente (LIMIT, STOP) dependem do user
    data stream para emitir fills posteriores — implementação prevista
    numa feature futura.
    """

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

        if order.fills and order.average_price is not None:
            await self.context.event_bus.publish(
                OrderFilled(
                    exchange_order_id=order.exchange_order_id,
                    symbol=order.symbol,
                    filled_quantity=float(order.executed_quantity),
                    average_price=float(order.average_price),
                    order=order,
                )
            )
            logger.info(
                "execution.ordered_and_filled",
                extra={
                    "order_id": order.exchange_order_id,
                    "symbol": order.symbol,
                    "num_fills": len(order.fills),
                    "executed_quantity": str(order.executed_quantity),
                },
            )
        else:
            logger.info(
                "execution.ordered_pending_fills",
                extra={
                    "order_id": order.exchange_order_id,
                    "symbol": order.symbol,
                    "status": order.status.value,
                },
            )

        return order