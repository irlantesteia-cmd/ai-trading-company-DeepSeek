import pytest

from app.features.engineering import (
    body_ratio,
    log_return,
    return_n,
    rsi,
    taker_buy_ratio,
    volume_zscore,
)


def test_return_1_known_values():
    closes = [100.0, 110.0, 99.0]
    out = return_n(closes, 1)
    assert out[0] is None
    assert out[1] == pytest.approx(0.10)
    assert out[2] == pytest.approx(-0.10)


def test_return_3_warmup():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    out = return_n(closes, 3)
    assert out[0] is None and out[1] is None and out[2] is None
    assert out[3] == pytest.approx(0.03)


def test_log_return_monotonic():
    closes = [100.0, 110.0]
    out = log_return(closes, 1)
    assert out[0] is None
    assert out[1] == pytest.approx(0.09531, rel=1e-4)


def test_rsi_all_gains_is_100():
    closes = list(range(100, 130))
    out = rsi(closes, 14)
    assert out[-1] == 100.0


def test_rsi_all_losses_is_0():
    closes = list(range(130, 100, -1))
    out = rsi(closes, 14)
    assert out[-1] == pytest.approx(0.0)


def test_rsi_warmup_is_none():
    closes = [100.0] * 5
    out = rsi(closes, 14)
    assert all(v is None for v in out)


def test_volume_zscore_constant_is_zero():
    vols = [100.0] * 30
    out = volume_zscore(vols, 20)
    assert out[-1] == 0.0


def test_volume_zscore_spike_positive():
    vols = [100.0] * 20 + [1000.0]
    out = volume_zscore(vols, 20)
    assert out[-1] is not None
    assert out[-1] > 3.0


def test_body_ratio_doji_is_zero():
    out = body_ratio(
        opens=[100.0],
        highs=[101.0],
        lows=[99.0],
        closes=[100.0],
    )
    assert out[0] == 0.0


def test_body_ratio_full_bullish():
    out = body_ratio(
        opens=[100.0],
        highs=[102.0],
        lows=[100.0],
        closes=[102.0],
    )
    assert out[0] == pytest.approx(1.0)


def test_taker_buy_ratio_known_values():
    out = taker_buy_ratio([100.0, 50.0], [40.0, 25.0])
    assert out[0] == pytest.approx(0.40)
    assert out[1] == pytest.approx(0.50)


def test_taker_buy_ratio_missing_or_zero_volume_is_none():
    out = taker_buy_ratio([0.0, 10.0, 10.0], [1.0, None, 4.0])
    assert out[0] is None
    assert out[1] is None
    assert out[2] == pytest.approx(0.40)


def test_taker_buy_ratio_clips_to_unit_interval():
    out = taker_buy_ratio([10.0, 10.0], [12.0, -1.0])
    assert out[0] == 1.0
    assert out[1] == 0.0


def test_taker_buy_ratio_length_mismatch():
    with pytest.raises(ValueError):
        taker_buy_ratio([1.0], [1.0, 2.0])