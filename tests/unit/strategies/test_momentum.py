import pytest

from app.core.enums import MarketType, SignalDirection
from app.strategies.context import StrategyContext
from app.strategies.momentum import MomentumStrategy
from tests.unit.strategies.conftest import make_candles


def _ctx(candles):
    return StrategyContext(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        candles=candles,
    )


def _find_first_signal(strategy, candles):
    for i in range(strategy.warmup, len(candles) + 1):
        sig = strategy.generate(_ctx(candles[:i]))
        if sig is not None:
            return i - 1, sig
    return None


def test_momentum_fires_long_after_flat_then_rise():
    closes = [100.0] * 20 + [100.0 + i for i in range(1, 15)]
    candles = make_candles(closes)
    strategy = MomentumStrategy(
        fast_period=3,
        slow_period=5,
        adx_period=3,
        adx_threshold=5.0,
        atr_period=3,
    )
    found = _find_first_signal(strategy, candles)
    assert found is not None, "esperava pelo menos um sinal LONG após a inflexão"
    _, sig = found
    assert sig.direction == SignalDirection.LONG


def test_momentum_rejects_when_fast_equal_slow():
    closes = [100.0] * 40
    candles = make_candles(closes)
    strategy = MomentumStrategy(
        fast_period=3,
        slow_period=5,
        adx_period=3,
        adx_threshold=1.0,
        atr_period=3,
    )
    for i in range(strategy.warmup, len(candles) + 1):
        assert strategy.generate(_ctx(candles[:i])) is None


def test_momentum_invalid_periods():
    with pytest.raises(ValueError):
        MomentumStrategy(fast_period=10, slow_period=5)


def test_momentum_signal_has_stop_below_entry_for_long():
    closes = [100.0] * 20 + [100.0 + i for i in range(1, 20)]
    candles = make_candles(closes)
    strategy = MomentumStrategy(
        fast_period=3,
        slow_period=5,
        adx_period=3,
        adx_threshold=5.0,
        atr_period=3,
    )
    found = _find_first_signal(strategy, candles)
    assert found is not None
    _, sig = found
    assert sig.suggested_entry is not None
    assert sig.suggested_stop is not None
    assert sig.suggested_target is not None
    assert sig.suggested_stop < sig.suggested_entry < sig.suggested_target