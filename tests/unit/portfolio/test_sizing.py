from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.enums import (
    MarketType,
    OrderSide,
    RiskAction,
    SignalDirection,
)
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.sizing import PositionSizer
from app.portfolio.state import PortfolioState


def _signal(
    *,
    entry: Decimal | None = Decimal(60000),
    stop: Decimal | None = Decimal(59000),
    direction: SignalDirection = SignalDirection.LONG,
) -> Signal:
    return Signal(
        signal_id="s-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        direction=direction,
        confidence=0.9,
        horizon="5m",
        suggested_entry=entry,
        suggested_stop=stop,
        strategy="momentum",
        agent="asset::BTCUSDT",
        generated_at=datetime.now(UTC),
    )


def _decision() -> RiskDecision:
    return RiskDecision(
        signal_id="s-1",
        action=RiskAction.APPROVE,
        reason="ok",
    )


def _state(equity: str = "10000") -> PortfolioState:
    return PortfolioState(equity=Decimal(equity), daily_pnl=Decimal(0))


def test_sizes_by_risk_per_trade():
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    intent = sizer.size(_signal(), _decision(), _state("10000"))
    assert intent.quantity == Decimal("0.10000000")
    assert intent.side is OrderSide.BUY
    assert intent.stop_price == Decimal(59000)


def test_caps_by_max_notional():
    sizer = PositionSizer(
        risk_per_trade_pct=0.50,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000),
    )
    intent = sizer.size(_signal(), _decision(), _state("10000"))
    assert intent.quantity == Decimal("0.01666666")


def test_short_direction():
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    signal = _signal(stop=Decimal(61000), direction=SignalDirection.SHORT)
    intent = sizer.size(signal, _decision(), _state("10000"))
    assert intent.side is OrderSide.SELL


def test_uses_default_stop_when_missing():
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    signal = _signal(stop=None)
    intent = sizer.size(signal, _decision(), _state("10000"))
    assert intent.quantity == Decimal("0.08333333")
    assert intent.stop_price == Decimal("58800.00")


def test_requires_suggested_entry():
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    with pytest.raises(ValueError, match="suggested_entry"):
        sizer.size(_signal(entry=None), _decision(), _state())


def test_rejects_stop_equal_entry():
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    with pytest.raises(ValueError, match="stop == entry"):
        sizer.size(
            _signal(entry=Decimal(60000), stop=Decimal(60000)),
            _decision(),
            _state(),
        )


def test_rejects_zero_or_negative_equity():
    sizer = PositionSizer(
        risk_per_trade_pct=0.01,
        default_stop_pct=0.02,
        max_notional_per_symbol=Decimal(1000000),
    )
    with pytest.raises(ValueError, match="equity"):
        sizer.size(_signal(), _decision(), _state("0"))
    with pytest.raises(ValueError, match="equity"):
        sizer.size(_signal(), _decision(), _state("-100"))