"""Testes da fase ML-2a: purge temporal + baseline no treino."""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.dataset import Dataset, temporal_split
from app.ml.training import train_classifier


def _make_dataset(n: int, *, horizon: int = 5, n_features: int = 3) -> Dataset:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(n, n_features)).astype(np.float64)
    y = (rng.random(n) > 0.5).astype(np.int64)
    return Dataset(
        X=X,
        y=y,
        feature_names=[f"f{i}" for i in range(n_features)],
        times=[None] * n,  # type: ignore[list-item]
        indices=list(range(n)),
        horizon=horizon,
    )


# ---------------------------------------------------------------- temporal_split
def test_split_purge_zero_matches_historical_behavior() -> None:
    ds = _make_dataset(100)
    train, test = temporal_split(ds, test_size=0.2, purge=0)
    assert len(train) == 80
    assert len(test) == 20


def test_split_purge_removes_from_end_of_train() -> None:
    ds = _make_dataset(100)
    train, test = temporal_split(ds, test_size=0.2, purge=5)
    assert len(train) == 75
    assert len(test) == 20
    # As 5 amostras removidas eram as últimas do treino: 75..79.
    # Sobrevivente mais tarde tem índice 74.
    assert train.indices[-1] == 74
    # Teste continua começando em 80.
    assert test.indices[0] == 80


def test_split_purge_too_large_raises() -> None:
    ds = _make_dataset(20)
    with pytest.raises(ValueError, match="elimina todo o conjunto"):
        temporal_split(ds, test_size=0.2, purge=20)


def test_split_purge_negative_raises() -> None:
    ds = _make_dataset(100)
    with pytest.raises(ValueError, match="purge"):
        temporal_split(ds, test_size=0.2, purge=-1)


# ------------------------------------------------------------ baseline no treino
def test_train_classifier_records_baseline_metrics() -> None:
    # y correlacionado com a primeira feature → modelo aprende algo
    rng = np.random.default_rng(1)
    n = 300
    X = rng.normal(size=(n, 3)).astype(np.float64)
    y = (X[:, 0] + 0.1 * rng.normal(size=n) > 0).astype(np.int64)
    ds = Dataset(
        X=X,
        y=y,
        feature_names=["a", "b", "c"],
        times=[None] * n,  # type: ignore[list-item]
        indices=list(range(n)),
        horizon=5,
    )

    result = train_classifier(ds, symbol="TESTUSDT")

    assert "baseline_accuracy" in result.test_metrics
    assert "baseline_auc" in result.test_metrics
    assert result.metadata.baseline_accuracy == pytest.approx(
        result.test_metrics["baseline_accuracy"]
    )
    assert result.metadata.baseline_auc == pytest.approx(
        result.test_metrics["baseline_auc"]
    )
    # Purge = horizon: n_train = 80% de 300 - 5 = 240 - 5 = 235
    assert result.metadata.n_train == 235
    assert result.metadata.n_test == 60
    # O baseline "prior" tem AUC exatamente 0.5 (predição constante).
    assert result.metadata.baseline_auc == pytest.approx(0.5)


def test_train_classifier_purge_in_hyperparams() -> None:
    ds = _make_dataset(200, horizon=3)
    result = train_classifier(ds, symbol="X")
    assert result.metadata.hyperparams["purge"] == 3