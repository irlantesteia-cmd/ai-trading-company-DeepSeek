import pytest

from app.strategies.indicators import adx, atr, ema, rolling_std, sma, zscore


def test_sma_basic():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == 2.0
    assert out[3] == 3.0
    assert out[4] == 4.0


def test_sma_period_too_large():
    assert sma([1, 2], 5) == [None, None]


def test_sma_invalid_period():
    with pytest.raises(ValueError):
        sma([1, 2, 3], 0)


def test_ema_basic():
    out = ema([1, 2, 3, 4, 5], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == 2.0  # seed = SMA(1,2,3)
    # k = 2 / (3+1) = 0.5
    assert out[3] == pytest.approx(4 * 0.5 + 2 * 0.5)  # 3.0
    assert out[4] == pytest.approx(5 * 0.5 + 3.0 * 0.5)  # 4.0


def test_rolling_std_constant_series_is_zero():
    out = rolling_std([10, 10, 10, 10], 3)
    assert out[2] == 0.0
    assert out[3] == 0.0


def test_zscore_of_flat_series_is_none():
    out = zscore([100, 100, 100, 100], 3)
    assert all(v is None for v in out)


def test_zscore_of_extreme_drop():
    closes = [100.0] * 10 + [80.0]
    z = zscore(closes, 5)
    # janela em i=10 = [100,100,100,100,80] → mean=96, std=8, z=(80-96)/8=-2
    assert z[10] == pytest.approx(-2.0)


def test_atr_of_flat_market_is_tiny():
    closes = [100.0] * 20
    highs = [100.1] * 20
    lows = [99.9] * 20
    out = atr(highs, lows, closes, 5)
    assert out[-1] is not None
    assert out[-1] < 0.5


def test_adx_high_in_monotonic_trend():
    closes = [100.0 + i for i in range(40)]
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    out = adx(highs, lows, closes, 7)
    assert out[-1] is not None
    assert out[-1] > 50


def test_adx_low_in_flat_market():
    closes = [100.0] * 40
    highs = [100.05] * 40
    lows = [99.95] * 40
    out = adx(highs, lows, closes, 7)
    assert out[-1] is not None
    assert out[-1] < 5