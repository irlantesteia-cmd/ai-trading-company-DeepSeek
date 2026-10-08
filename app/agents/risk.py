from __future__ import annotations

import logging
from decimal import Decimal

from app.agents.base import BaseAgent
from app.core.enums import AgentRole
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits

logger = logging.getLogger(__name__)


def _limits_from_settings(settings) -> RiskLimits:
    return RiskLimits(
        max_position_notional_per_symbol=Decimal(
            str(settings.risk_max_position_notional)
        ),
        max_total_notional=Decimal(str(settings.risk_max_total_notional)),
        max_leverage=settings.risk_max_leverage,
        max_daily_loss=Decimal(str(settings.risk_max_daily_loss)),
        max_open_positions=settings.risk_max_open_positions,
        max_same_direction=settings.risk_max_same_direction,
        min_confidence=settings.risk_min_confidence,
        correlated_groups=dict(settings.risk_correlated_groups),
    )


class RiskAgent(BaseAgent):
    """Avalia sinais via RiskEngine.

    Aceita `state` injetado (útil em testes e no pipeline) ou carrega da exchange.
    """

    role = AgentRole.RISK
    name = "risk_officer"

    def __init__(self, context, *, engine: RiskEngine | None = None) -> None:
        super().__init__(context)
        self._engine = engine or RiskEngine(_limits_from_settings(context.settings))

    @property
    def engine(self) -> RiskEngine:
        return self._engine

    async def evaluate(
        self,
        signal: Signal,
        state: PortfolioState | None = None,
    ) -> RiskDecision:
        if state is None:
            state = await self.load_state()
        return await self._engine.evaluate(signal, state)

    async def load_state(self) -> PortfolioState:
        positions = await self.context.exchange.account.get_futures_positions()
        balance = await self.context.exchange.account.get_futures_balance("USDT")
        return PortfolioState(
            equity=balance.total,
            daily_pnl=Decimal(0),  # rastreamento real entra na Fase 7
            positions=positions,
        )