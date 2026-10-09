"""Testes da fase ML-3b: walk-forward validation."""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.dataset import Dataset, walk_forward_splits
from app.ml.training import (
    _compute_deployable_wf,
    train_walk_forward,
)


def _random_dataset(n: int = 600, n_features: int = 8, horizon: int = 5) -> Dataset:
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


def _signal_dataset(n: int = 600, n_features: int = 8, horizon: int = 5) -> Dataset:
    """Dataset com sinal determinístico (y = f(X[:,0])) para o AUC ser alto."""
    rng = np.random.default_rng(1)
    X = rng.normal(size=(n, n_features)).astype(np.float64)
    # Sinal forte e estável: y = 1 se X[:,0] > 0
    y = (X[:, 0] + 0.05 * rng.normal(size=n) > 0).astype(np.int64)
    return Dataset(
        X=X,
        y=y,
        feature_names=[f"f{i}" for i in range(n_features)],
        times=[None] * n,  # type: ignore[list-item]
        indices=list(range(n)),
        horizon=horizon,
    )


# ------------------------------------------------------- walk_forward_splits
def test_walk_forward_produces_n_folds() -> None:
    ds = _random_dataset(600)
    folds = walk_forward_splits(ds, n_folds=5, min_train_size=100, min_test_size=20)
    assert len(folds) == 5


def test_walk_forward_train_grows_test_advances() -> None:
    ds = _random_dataset(600)
    folds = walk_forward_splits(ds, n_folds=5, min_train_size=100, min_test_size=20)
    # Treino cresce ou mantém; teste avança.
    train_lens = [len(tr) for tr, _ in folds]
    test_starts = [te.indices[0] for _, te in folds]
    assert train_lens == sorted(train_lens)
    assert test_starts == sorted(test_starts)
    # Cada teste não vazio
    for _, te in folds:
        assert len(te) > 0


def test_walk_forward_respects_purge() -> None:
    ds = _random_dataset(600, horizon=5)
    folds = walk_forward_splits(ds, n_folds=3, min_train_size=100, min_test_size=20)
    for train, test in folds:
        # Última amostra do treino termina antes do primeiro teste
        last_train_idx = train.indices[-1]
        first_test_idx = test.indices[0]
        assert first_test_idx - last_train_idx >= ds.horizon


def test_walk_forward_too_small_raises() -> None:
    ds = _random_dataset(50)
    with pytest.raises(ValueError, match="muito pequeno"):
        walk_forward_splits(ds, n_folds=5, min_train_size=100, min_test_size=20)


def test_walk_forward_invalid_n_folds() -> None:
    ds = _random_dataset(600)
    with pytest.raises(ValueError, match="n_folds"):
        walk_forward_splits(ds, n_folds=1)


# ------------------------------------------------------------ deploy gate WF
def test_deployable_wf_approves_solid() -> None:
    ok, reason = _compute_deployable_wf(
        mean_auc=0.60,
        std_auc=0.03,
        fold_aucs=[0.58, 0.61, 0.63, 0.59, 0.61],
        min_deploy_auc=0.55,
        max_std=0.10,
    )
    assert ok is True
    assert reason is None


def test_deployable_wf_rejects_low_mean() -> None:
    ok, reason = _compute_deployable_wf(
        mean_auc=0.52,
        std_auc=0.03,
        fold_aucs=[0.51, 0.52, 0.53],
        min_deploy_auc=0.55,
        max_std=0.10,
    )
    assert ok is False
    assert reason is not None
    assert "wf_mean_auc_below_min" in reason


def test_deployable_wf_rejects_high_std() -> None:
    ok, reason = _compute_deployable_wf(
        mean_auc=0.60,
        std_auc=0.15,
        fold_aucs=[0.40, 0.70, 0.65],
        min_deploy_auc=0.55,
        max_std=0.10,
    )
    assert ok is False
    assert reason is not None
    assert "wf_std_above_max" in reason


def test_deployable_wf_rejects_fold_below_baseline() -> None:
    ok, reason = _compute_deployable_wf(
        mean_auc=0.62,
        std_auc=0.09,
        fold_aucs=[0.65, 0.62, 0.45, 0.68],
        min_deploy_auc=0.55,
        max_std=0.10,
    )
    assert ok is False
    assert reason is not None
    assert "wf_fold_below_baseline" in reason


# ----------------------------------------------------------- train_walk_forward
def test_train_walk_forward_on_signal_dataset_approves() -> None:
    """Dataset com sinal forte e estável → mean_auc alto → deployable."""
    ds = _signal_dataset(n=600)
    result = train_walk_forward(
        ds,
        symbol="TESTUSDT",
        n_folds=5,
        min_train_size=100,
        min_deploy_auc=0.55,
        max_std=0.10,
    )
    assert result.metadata.deployable is True
    assert result.test_metrics["auc"] == pytest.approx(
        result.test_metrics["wf_mean_auc"]
    )
    assert len(result.metadata.walk_forward_aucs) == 5


def test_train_walk_forward_on_random_dataset_rejects() -> None:
    """Dataset aleatório → AUC ≈ 0.5 → deployable=False."""
    ds = _random_dataset(n=600)
    result = train_walk_forward(
        ds,
        symbol="TESTUSDT",
        n_folds=5,
        min_train_size=100,
        min_deploy_auc=0.55,
        max_std=0.10,
    )
    assert result.metadata.deployable is False
    assert result.metadata.deploy_reason is not None