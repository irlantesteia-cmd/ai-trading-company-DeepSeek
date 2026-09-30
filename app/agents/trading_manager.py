from __future__ import annotations

import logging
from decimal import Decimal

from app.agents.base import BaseAgent
from app.core.enums import AgentRole, RiskAction
from app.domain.models.order import Order
from app.domain.models.signal import Signal
from app.events.event import SignalApproved, SignalRejected
from app.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)


class TradingManager(BaseAgent):
    """Orquestra o pipeline: Signal → Risk → Portfolio → Execution."""

    role = AgentRole.TRADING_MANAGER
    name = "trading_manager"

    async def process_signal(self, signal: Signal) -> Order | None:
        registry = self.context.registry

        risk = registry.require_one(AgentRole.RISK)
        portfolio = registry.require_one(AgentRole.PORTFOLIO)
        execution = registry.require_one(AgentRole.EXECUTION)

        state = await self._load_state()
        decision = await risk.evaluate(signal, state)  # type: ignore[attr-defined]

        if decision.action == RiskAction.REJECT:
            await self.context.event_bus.publish(
                SignalRejected(signal_id=signal.signal_id, reason=decision.reason)
            )
            logger.info(
                "trading.signal_rejected",
                extra={"signal_id": signal.signal_id, "reason": decision.reason},
            )
            return None

        intent = await portfolio.size(signal, decision, state)  # type: ignore[attr-defined]

        await self.context.event_bus.publish(
            SignalApproved(
                signal_id=signal.signal_id,
                approved_quantity=float(intent.quantity),
            )
        )

        order = await execution.execute(intent)  # type: ignore[attr-defined]

        logger.info(
            "trading.order_placed",
            extra={
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "order_id": order.exchange_order_id,
                "quantity": str(intent.quantity),
            },
        )
        return order

    async def _load_state(self) -> PortfolioState:
        positions = await self.context.exchange.account.get_futures_positions()
        balance = await self.context.exchange.account.get_futures_balance("USDT")
        return PortfolioState(
            equity=balance.total,
            daily_pnl=Decimal(0),
            positions=positions,
        )