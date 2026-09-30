from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents.portfolio import PortfolioAgent
from app.core.enums import MarketType, OrderSide, RiskAction, SignalDirection
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.sizing import PositionSizer
from app.portfolio.state import PortfolioState


def _signal() -> Signal:
    return Signal(
        signal_id="s-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        confidence=0.9,
        horizon="5m",
        suggested_entry=Decimal(60000),
        suggested_stop=Decimal(59000),
        strategy="momentum",
        agent="asset::BTCUSDT",
        generated_at=datetime.now(UTC),
    )


def _decision() -> RiskDecision:
    return RiskDecision(signal_id="s-1", action=RiskAction.APPROVE, reason="ok")


def _state() -> PortfolioState:
    return PortfolioState(equity=Decimal(10000), daily_pnl=Decimal(0))


@pytest.mark.asyncio
async def test_uses_injected_sizer(context):
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    agent = PortfolioAgent(context, sizer=sizer)
    intent = await agent.size(_signal(), _decision(), _state())
    assert intent.quantity == Decimal("0.10000000")
    assert intent.side is OrderSide.BUY


@pytest.mark.asyncio
async def test_loads_state_from_exchange(context):
    agent = PortfolioAgent(context)
    intent = await agent.size(_signal(), _decision())
    assert intent.quantity > 0