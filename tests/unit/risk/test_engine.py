from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.enums import (
    MarginType,
    MarketType,
    PositionSide,
    RiskAction,
    SignalDirection,
)
from app.domain.models.position import FuturesPosition
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits


def _signal(
    *,
    confidence: float = 0.9,
    symbol: str = "BTCUSDT",
) -> Signal:
    return Signal(
        signal_id="s-1",
        symbol=symbol,
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


def _position(
    symbol: str = "BTCUSDT", notional_price: str = "60000"
) -> FuturesPosition:
    return FuturesPosition(
        symbol=symbol,
        position_side=PositionSide.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal(notional_price),
        mark_price=Decimal(notional_price),
        unrealized_pnl=Decimal(0),
        realized_pnl=Decimal(0),
        leverage=5,
        margin_type=MarginType.ISOLATED,
        isolated_margin=Decimal(500),
        updated_at=datetime.now(UTC),
    )


def _state(
    *,
    equity: str = "10000",
    daily_pnl: str = "0",
    positions: list[FuturesPosition] | None = None,
) -> PortfolioState:
    return PortfolioState(
        equity=Decimal(equity),
        daily_pnl=Decimal(daily_pnl),
        positions=positions or [],
    )


def _engine(**overrides) -> RiskEngine:
    defaults: dict = {
        "max_position_notional_per_symbol": Decimal(10000),
        "max_total_notional": Decimal(50000),
        "max_leverage": 10,
        "max_daily_loss": Decimal(1000),
        "max_open_positions": 5,
        "min_confidence": 0.5,
        "correlated_groups": {"crypto_majors": ["BTCUSDT", "ETHUSDT"]},
    }
    defaults.update(overrides)
    return RiskEngine(RiskLimits(**defaults))


@pytest.mark.asyncio
async def test_approves_clean_signal():
    decision = await _engine().evaluate(_signal(), _state())
    assert decision.action is RiskAction.APPROVE


@pytest.mark.asyncio
async def test_rejects_low_confidence():
    decision = await _engine().evaluate(_signal(confidence=0.2), _state())
    assert decision.action is RiskAction.REJECT
    assert "confidence" in decision.reason


@pytest.mark.asyncio
async def test_rejects_daily_loss_exceeded():
    decision = await _engine().evaluate(_signal(), _state(daily_pnl="-1500"))
    assert decision.action is RiskAction.REJECT
    assert "daily loss" in decision.reason


@pytest.mark.asyncio
async def test_rejects_max_open_positions():
    engine = _engine(max_open_positions=1)
    state = _state(positions=[_position("SOLUSDT")])
    decision = await engine.evaluate(_signal(), state)
    assert decision.action is RiskAction.REJECT
    assert "max_open_positions" in decision.reason


@pytest.mark.asyncio
async def test_rejects_same_symbol_already_open():
    state = _state(positions=[_position("BTCUSDT")])
    decision = await _engine().evaluate(_signal(symbol="BTCUSDT"), state)
    assert decision.action is RiskAction.REJECT
    assert "posição já aberta" in decision.reason


@pytest.mark.asyncio
async def test_rejects_correlated_group():
    state = _state(positions=[_position("BTCUSDT")])
    decision = await _engine().evaluate(_signal(symbol="ETHUSDT"), state)
    assert decision.action is RiskAction.REJECT
    assert "correlação" in decision.reason


@pytest.mark.asyncio
async def test_correlation_does_not_block_uncorrelated():
    engine = _engine(correlated_groups={"crypto_majors": ["BTCUSDT", "ETHUSDT"]})
    state = _state(positions=[_position("SOLUSDT")])
    decision = await engine.evaluate(_signal(symbol="BTCUSDT"), state)
    assert decision.action is RiskAction.APPROVE


@pytest.mark.asyncio
async def test_approve_sets_max_leverage():
    decision = await _engine(max_leverage=7).evaluate(_signal(), _state())
    assert decision.action is RiskAction.APPROVE
    assert decision.max_leverage == 7