import pytest

from app.core.enums import MarketType, SignalDirection
from app.strategies.context import StrategyContext
from app.strategies.mean_reversion import MeanReversionStrategy
from tests.unit.strategies.conftest import make_candles


def _ctx(candles):
    return StrategyContext(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        candles=candles,
    )


def _reversion_strategy(**overrides) -> MeanReversionStrategy:
    params: dict = {
        "lookback": 5,
        "entry_z": 2.0,
        "adx_period": 3,
        "adx_threshold": 200.0,  # desabilita filtro de ADX
        "atr_period": 3,
        "stop_atr_mult": 2.0,
    }
    params.update(overrides)
    return MeanReversionStrategy(**params)


def test_mean_reversion_fires_long_on_extreme_drop():
    closes = [100.0] * 20 + [80.0]
    candles = make_candles(closes)
    sig = _reversion_strategy().generate(_ctx(candles))
    assert sig is not None
    assert sig.direction == SignalDirection.LONG


def test_mean_reversion_fires_short_on_extreme_spike():
    closes = [100.0] * 20 + [120.0]
    candles = make_candles(closes)
    sig = _reversion_strategy().generate(_ctx(candles))
    assert sig is not None
    assert sig.direction == SignalDirection.SHORT


def test_mean_reversion_returns_none_when_z_insufficient():
    closes = [100.0] * 25
    candles = make_candles(closes)
    sig = _reversion_strategy().generate(_ctx(candles))
    assert sig is None


def test_mean_reversion_blocks_when_adx_high():
    closes = [100.0] * 20 + [80.0]
    candles = make_candles(closes)
    # threshold = -1 → qualquer ADX >= -1 bloqueia
    strategy = _reversion_strategy(adx_threshold=-1.0)
    sig = strategy.generate(_ctx(candles))
    assert sig is None


def test_mean_reversion_short_stop_above_entry():
    closes = [100.0] * 20 + [120.0]
    candles = make_candles(closes)
    sig = _reversion_strategy().generate(_ctx(candles))
    assert sig is not None
    assert sig.suggested_entry is not None
    assert sig.suggested_stop is not None
    assert sig.suggested_stop > sig.suggested_entry


@pytest.mark.parametrize("direction", [SignalDirection.LONG, SignalDirection.SHORT])
def test_mean_reversion_target_is_mean(direction):
    closes = [100.0] * 20 + ([80.0] if direction == SignalDirection.LONG else [120.0])
    candles = make_candles(closes)
    sig = _reversion_strategy().generate(_ctx(candles))
    assert sig is not None
    assert sig.direction == direction
    assert sig.suggested_target is not None
    # alvo é a SMA(lookback), que fica entre 80 e 100 (ou 100 e 120)
    target = float(sig.suggested_target)
    if direction == SignalDirection.LONG:
        assert 80.0 < target < 100.0
    else:
        assert 100.0 < target < 120.0