"""Testes da fase ML-2b-2: filtro de magnitude no label."""

from __future__ import annotations

import pytest

from app.features.pipeline import default_pipeline
from app.ml.dataset import build_dataset, temporal_split
from tests.unit.strategies.conftest import make_candles


def _closes_with_step(n: int, step_pct: float) -> list[float]:
    """Série geométrica com passo fixo. `step_pct` é fracional (0.005 = 0.5%)."""
    return [100.0 * ((1.0 + step_pct) ** i) for i in range(n)]


def test_default_keeps_all_samples() -> None:
    candles = make_candles(_closes_with_step(80, 0.005))
    ds_default = build_dataset(candles, pipeline=default_pipeline(), horizon=5)
    ds_zero = build_dataset(
        candles, pipeline=default_pipeline(), horizon=5, min_return_pct=0.0
    )
    assert len(ds_default) == len(ds_zero)
    assert ds_default.min_return_pct == 0.0


def test_filters_micro_moves() -> None:
    # Passo de 0.01% → forward de 5 candles ≈ 0.05%, abaixo do threshold 0.1%.
    candles = make_candles(_closes_with_step(80, 0.0001))
    ds = build_dataset(
        candles, pipeline=default_pipeline(), horizon=5, min_return_pct=0.001
    )
    assert len(ds) == 0


def test_keeps_large_returns() -> None:
    # Passo de 0.5% → forward de 5 candles ≈ 2.5%, muito acima do threshold.
    candles = make_candles(_closes_with_step(80, 0.005))
    ds = build_dataset(
        candles, pipeline=default_pipeline(), horizon=5, min_return_pct=0.001
    )
    assert len(ds) > 0


def test_dataset_records_min_return_pct() -> None:
    candles = make_candles(_closes_with_step(80, 0.005))
    ds = build_dataset(
        candles, pipeline=default_pipeline(), horizon=5, min_return_pct=0.002
    )
    assert ds.min_return_pct == 0.002


def test_temporal_split_propagates_min_return_pct() -> None:
    candles = make_candles(_closes_with_step(80, 0.005))
    ds = build_dataset(
        candles, pipeline=default_pipeline(), horizon=5, min_return_pct=0.002
    )
    train, test = temporal_split(ds, test_size=0.2)
    assert train.min_return_pct == 0.002
    assert test.min_return_pct == 0.002


def test_negative_min_return_pct_raises() -> None:
    candles = make_candles(_closes_with_step(80, 0.005))
    with pytest.raises(ValueError, match="min_return_pct"):
        build_dataset(
            candles,
            pipeline=default_pipeline(),
            horizon=5,
            min_return_pct=-0.001,
        )