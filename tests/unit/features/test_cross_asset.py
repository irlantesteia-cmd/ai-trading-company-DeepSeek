"""Testes do CrossAssetPipeline (ML-3d-1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models.market import Candle
from app.features.cross_asset import CrossAssetPipeline
from app.features.pipeline import default_pipeline


def _make_candles(
    closes: list[float],
    *,
    symbol: str = "ETHUSDT",
    start: datetime | None = None,
    interval_s: int = 300,
) -> list[Candle]:
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    for i, c in enumerate(closes):
        t = start + timedelta(seconds=interval_s * i)
        out.append(
            Candle(
                symbol=symbol,
                market_type="FUTURES",  # type: ignore[arg-type]
                interval="5m",
                open_time=t,
                close_time=t + timedelta(seconds=interval_s),
                open=str(c),
                high=str(c * 1.001),
                low=str(c * 0.999),
                close=str(c),
                volume="100",
                trades=10,
                closed=True,
            )
        )
    return out


def _base_pipeline():
    return default_pipeline()


def test_names_include_ref_features() -> None:
    p = CrossAssetPipeline(
        _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[1, 3, 5]
    )
    names = p.names
    assert "btc_return_1" in names
    assert "btc_return_3" in names
    assert "btc_return_5" in names
    # Base features continuam
    assert "return_1" in names
    assert "rsi_14" in names


def test_transform_produces_extra_columns() -> None:
    closes = [100.0 + i for i in range(50)]
    candles = _make_candles(closes, symbol="ETHUSDT")
    ref_closes = [50000.0 + i * 10 for i in range(50)]
    ref = _make_candles(ref_closes, symbol="BTCUSDT")

    p = CrossAssetPipeline(
        _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[1, 3]
    )
    fm = p.transform(candles, ref_candles={"BTCUSDT": ref})

    assert fm.values.shape[0] > 0
    assert fm.values.shape[1] == len(p.names)
    assert len(fm.names) == len(_base_pipeline().names) + 2


def test_transform_requires_ref() -> None:
    closes = [100.0 + i for i in range(50)]
    candles = _make_candles(closes)
    p = CrossAssetPipeline(_base_pipeline(), ref_symbol="BTCUSDT")
    with pytest.raises(ValueError, match="ref_candles"):
        p.transform(candles, ref_candles=None)


def test_transform_empty_when_ref_too_short() -> None:
    closes = [100.0 + i for i in range(50)]
    candles = _make_candles(closes)
    ref = _make_candles([50000.0, 50010.0])
    p = CrossAssetPipeline(
        _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[5]
    )
    fm = p.transform(candles, ref_candles={"BTCUSDT": ref})
    assert fm.values.shape[0] == 0


def test_transform_drops_rows_without_ref_match() -> None:
    closes = [100.0 + i for i in range(50)]
    candles = _make_candles(closes, symbol="ETHUSDT")
    # Ref começa 10 candles depois — só as últimas 40 têm match.
    later = datetime(2026, 1, 1, 0, 50, tzinfo=UTC)
    ref = _make_candles(
        [50000.0 + i * 10 for i in range(40)],
        symbol="BTCUSDT",
        start=later,
    )
    p = CrossAssetPipeline(
        _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[1]
    )
    fm = p.transform(candles, ref_candles={"BTCUSDT": ref})
    # Só amostras cujo open_time bate com o ref são mantidas.
    assert fm.values.shape[0] > 0
    assert fm.values.shape[0] <= 40


def test_ref_horizon_values_are_correct() -> None:
    """btc_return_1 deve ser (close_t / close_{t-1}) - 1."""
    closes = [100.0 + i for i in range(30)]
    candles = _make_candles(closes, symbol="ETHUSDT")
    ref_closes = [1000.0 * (1.01 ** i) for i in range(30)]
    ref = _make_candles(ref_closes, symbol="BTCUSDT")

    p = CrossAssetPipeline(
        _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[1]
    )
    fm = p.transform(candles, ref_candles={"BTCUSDT": ref})

    # Última linha da matrix deve ter btc_return_1 ≈ 0.01
    idx = fm.names.index("btc_return_1")
    last = fm.values[-1, idx]
    assert last == pytest.approx(0.01, abs=1e-6)


def test_invalid_ref_symbol_raises() -> None:
    with pytest.raises(ValueError, match="ref_symbol"):
        CrossAssetPipeline(_base_pipeline(), ref_symbol="")


def test_invalid_horizons_raise() -> None:
    with pytest.raises(ValueError, match="ref_horizons"):
        CrossAssetPipeline(
            _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[0, -1]
        )


def test_empty_main_candles_returns_empty() -> None:
    p = CrossAssetPipeline(
        _base_pipeline(), ref_symbol="BTCUSDT", ref_horizons=[1]
    )
    fm = p.transform([], ref_candles={"BTCUSDT": []})
    assert fm.values.shape[0] == 0
    assert fm.names == p.names


def test_default_pipeline_ignores_ref_candles() -> None:
    """`FeaturePipeline` puro aceita `ref_candles` e ignora."""
    closes = [100.0 + i for i in range(50)]
    candles = _make_candles(closes)
    p = _base_pipeline()
    fm_with = p.transform(candles, ref_candles={"BTCUSDT": candles})
    fm_without = p.transform(candles)
    assert fm_with.values.shape == fm_without.values.shape