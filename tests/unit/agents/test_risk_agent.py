from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents.risk import RiskAgent
from app.core.enums import MarketType, RiskAction, SignalDirection
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits


def _signal(confidence: float = 0.9) -> Signal:
    return Signal(
        signal_id="s-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        confidence=confidence,
        horizon="5m",
        suggested_entry=Decimal(60000),
        suggested_stop=Decimal(59000),
        strategy="momentum",
        agent="asset::BTCUSDT",
        generated_at=datetime.now(UTC),
    )


def _state() -> PortfolioState:
    return PortfolioState(equity=Decimal(10000), daily_pnl=Decimal(0))


@pytest.mark.asyncio
async def test_uses_injected_engine(context):
    engine = RiskEngine(
        RiskLimits(
            min_confidence=0.99,
            correlated_groups={},
        )
    )
    agent = RiskAgent(context, engine=engine)
    decision = await agent.evaluate(_signal(confidence=0.5), _state())
    assert decision.action is RiskAction.REJECT


@pytest.mark.asyncio
async def test_approves_when_state_injected(context):
    agent = RiskAgent(context)
    decision = await agent.evaluate(_signal(), _state())
    assert decision.action is RiskAction.APPROVE


@pytest.mark.asyncio
async def test_load_state_from_exchange(context):
    agent = RiskAgent(context)
    state = await agent.load_state()
    assert state.equity == Decimal(10000)
    assert state.open_positions_count == 0