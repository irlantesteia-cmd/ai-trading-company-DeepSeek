from __future__ import annotations

import logging
from decimal import Decimal

from app.agents.base import BaseAgent
from app.core.enums import AgentRole, RiskAction
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.portfolio.state_loader import load_portfolio_state
from app.risk.engine import AUTO_HALT_RULES, RiskEngine
from app.risk.limits import RiskLimits
from app.runtime.halt_guard import HaltGuard

logger = logging.getLogger(__name__)


def _limits_from_settings(settings) -> RiskLimits:
    return RiskLimits(
        max_position_notional_per_symbol=Decimal(
            str(settings.risk_max_position_notional)
        ),
        max_total_notional=Decimal(str(settings.risk_max_total_notional)),
        max_leverage=settings.risk_max_leverage,
        max_daily_loss=Decimal(str(settings.risk_max_daily_loss)),
        max_daily_loss_pct=Decimal(str(settings.risk_max_daily_loss_pct)),
        max_consecutive_losses=settings.risk_max_consecutive_losses,
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
        decision = await self._engine.evaluate(signal, state)
        if decision.action is RiskAction.REJECT and decision.rule in AUTO_HALT_RULES:
            await self._auto_halt(decision)
        return decision

    async def _auto_halt(self, decision: RiskDecision) -> None:
        """Limite crítico estourado → trava global persistente (§22 do projeto:
        TRADING HALTED; só libera por `scripts/trading_control.py resume`)."""
        if decision.rule is None:
            return
        guard = HaltGuard(
            session_factory=self.context.session_factory,
            event_bus=self.context.event_bus,
            created_by=self.name,
        )
        await guard.trip(rule=decision.rule, reason=decision.reason)

    async def load_state(self) -> PortfolioState:
        return await load_portfolio_state(
            self.context.exchange, self.context.session_factory
        )