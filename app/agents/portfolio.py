from __future__ import annotations

import logging
from decimal import Decimal

from app.agents.base import BaseAgent
from app.core.enums import AgentRole
from app.domain.models.order_intent import OrderIntent
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.sizing import PositionSizer
from app.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)


def _sizer_from_settings(settings) -> PositionSizer:
    return PositionSizer(
        risk_per_trade_pct=settings.sizing_risk_per_trade_pct,
        default_stop_pct=settings.sizing_default_stop_pct,
        max_notional_per_symbol=Decimal(str(settings.risk_max_position_notional)),
    )


class PortfolioAgent(BaseAgent):
    """Dimensiona a ordem a partir da decisão de risco + estado do portfólio."""

    role = AgentRole.PORTFOLIO
    name = "portfolio_manager"

    def __init__(self, context, *, sizer: PositionSizer | None = None) -> None:
        super().__init__(context)
        self._sizer = sizer or _sizer_from_settings(context.settings)

    @property
    def sizer(self) -> PositionSizer:
        return self._sizer

    async def size(
        self,
        signal: Signal,
        decision: RiskDecision,
        state: PortfolioState | None = None,
    ) -> OrderIntent:
        if state is None:
            state = await self._load_state()
        return self._sizer.size(signal, decision, state)

    async def _load_state(self) -> PortfolioState:
        positions = await self.context.exchange.account.get_futures_positions()
        balance = await self.context.exchange.account.get_futures_balance("USDT")
        return PortfolioState(
            equity=balance.total,
            daily_pnl=Decimal(0),
            positions=positions,
        )