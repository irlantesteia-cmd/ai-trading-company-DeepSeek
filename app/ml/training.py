from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from sklearn.dummy import DummyClassifier
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.ml.dataset import Dataset, temporal_split, walk_forward_splits
from app.ml.model import ForwardReturnClassifier, ModelMetadata, make_version


@dataclass(frozen=True)
class TrainingResult:
    model: ForwardReturnClassifier
    metadata: ModelMetadata
    train_accuracy: float
    test_accuracy: float
    test_metrics: dict[str, float]


def _safe_auc(y_true: NDArray[np.int64], proba: NDArray[np.float64]) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, proba))


def _compute_deployable(
    auc: float, min_deploy_auc: float
) -> tuple[bool, str | None]:
    """Decide se um modelo com este AUC pode ser deployado.

    `auc >= min_deploy_auc` (default 0.5). `auc=NaN` nunca deploya.
    """
    if math.isnan(auc):
        return False, "auc_nan"
    if auc < min_deploy_auc:
        return False, f"auc_below_min ({auc:.4f} < {min_deploy_auc})"
    return True, None


def _compute_deployable_wf(
    *,
    mean_auc: float,
    std_auc: float,
    fold_aucs: list[float],
    min_deploy_auc: float,
    max_std: float,
) -> tuple[bool, str | None]:
    """Gate de deploy para walk-forward.

    Requer, cumulativamente:
      - nenhum NaN entre os folds;
      - `mean_auc >= min_deploy_auc`;
      - `std_auc <= max_std` (modelo consistente entre folds);
      - `min(fold_aucs) > 0.5` (nenhum fold cai abaixo do baseline).
    """
    if not fold_aucs:
        return False, "wf_no_folds"
    if any(math.isnan(a) for a in fold_aucs):
        return False, "wf_auc_nan"
    if mean_auc < min_deploy_auc:
        return False, f"wf_mean_auc_below_min ({mean_auc:.4f} < {min_deploy_auc})"
    if std_auc > max_std:
        return False, f"wf_std_above_max ({std_auc:.4f} > {max_std})"
    min_fold = min(fold_aucs)
    if min_fold <= 0.5:
        return False, f"wf_fold_below_baseline ({min_fold:.4f} <= 0.5)"
    return True, None


def _baseline_metrics(
    train: Dataset,
    test: Dataset,
    *,
    random_state: int,
) -> tuple[float, float]:
    """Retorna (baseline_accuracy, baseline_auc) de um `DummyClassifier`."""
    baseline = DummyClassifier(strategy="prior", random_state=random_state)
    baseline.fit(train.X, train.y)

    baseline_pred = baseline.predict(test.X)
    baseline_accuracy = float(accuracy_score(test.y, baseline_pred))

    proba = baseline.predict_proba(test.X)
    classes = baseline.classes_
    idx_up = int(np.where(classes == 1)[0][0]) if 1 in classes else 0
    baseline_auc = _safe_auc(test.y, proba[:, idx_up])

    return baseline_accuracy, baseline_auc


def train_classifier(
    dataset: Dataset,
    *,
    symbol: str,
    test_size: float = 0.2,
    random_state: int = 42,
    C: float = 1.0,
    max_iter: int = 2000,
    min_deploy_auc: float = 0.5,
) -> TrainingResult:
    """Treino por split único. Mantido para retrocompatibilidade e
    para avaliação rápida em desenvolvimento. O `MLAgent` usa
    `train_walk_forward` por padrão.
    """
    if len(dataset) < 10:
        raise ValueError(f"dataset muito pequeno: {len(dataset)} amostras")

    train, test = temporal_split(
        dataset, test_size=test_size, purge=dataset.horizon
    )
    n_purged = len(dataset) - len(train) - len(test)

    model = ForwardReturnClassifier(
        random_state=random_state, C=C, max_iter=max_iter
    )
    model.fit(train.X, train.y)

    train_pred = model.predict(train.X)
    test_pred = model.predict(test.X)
    test_proba = model.proba_up(test.X)

    baseline_accuracy, baseline_auc = _baseline_metrics(
        train, test, random_state=random_state
    )

    auc = _safe_auc(test.y, test_proba)
    deployable, deploy_reason = _compute_deployable(auc, min_deploy_auc)

    test_metrics = {
        "accuracy": float(accuracy_score(test.y, test_pred)),
        "precision": float(precision_score(test.y, test_pred, zero_division=0)),
        "recall": float(recall_score(test.y, test_pred, zero_division=0)),
        "f1": float(f1_score(test.y, test_pred, zero_division=0)),
        "auc": auc,
        "baseline_accuracy": baseline_accuracy,
        "baseline_auc": baseline_auc,
    }

    metadata = ModelMetadata(
        version=make_version(symbol, dataset.horizon),
        symbol=symbol,
        horizon=dataset.horizon,
        feature_names=dataset.feature_names,
        trained_at=datetime.now(UTC).isoformat(),
        n_train=len(train),
        n_test=len(test),
        metrics=test_metrics,
        hyperparams={
            "C": C,
            "max_iter": max_iter,
            "random_state": random_state,
            "test_size": test_size,
            "purge": dataset.horizon,
            "min_deploy_auc": min_deploy_auc,
            "label_min_return_pct": dataset.min_return_pct,
            "training_mode": "single_split",
        },
        baseline_accuracy=baseline_accuracy,
        baseline_auc=baseline_auc,
        n_purged=n_purged,
        deployable=deployable,
        deploy_reason=deploy_reason,
    )

    return TrainingResult(
        model=model,
        metadata=metadata,
        train_accuracy=float(accuracy_score(train.y, train_pred)),
        test_accuracy=float(accuracy_score(test.y, test_pred)),
        test_metrics=test_metrics,
    )


def train_walk_forward(
    dataset: Dataset,
    *,
    symbol: str,
    n_folds: int = 5,
    min_train_size: int = 100,
    min_test_size: int = 20,
    random_state: int = 42,
    C: float = 1.0,
    max_iter: int = 2000,
    min_deploy_auc: float = 0.5,
    max_std: float = 0.10,
) -> TrainingResult:
    """Treina o modelo em expanding-window walk-forward e agrega AUC.

    Modelo persistido: treinado no **último fold** (o mais recente = o
    mais próximo do regime atual de mercado).

    Métricas de deploy:
      - `mean_auc`, `std_auc`, `min_auc` sobre os folds.
      - Gate: `mean_auc >= min_deploy_auc`, `std_auc <= max_std`,
        `min(fold_aucs) > 0.5`.
      - `metrics["auc"]` = `mean_auc` (o `MLStrategy` lê essa chave).
    """
    folds = walk_forward_splits(
        dataset,
        n_folds=n_folds,
        min_train_size=min_train_size,
        min_test_size=min_test_size,
    )

    fold_aucs: list[float] = []
    last_model: ForwardReturnClassifier | None = None
    last_train: Dataset | None = None
    last_test: Dataset | None = None

    for i, (train, test) in enumerate(folds):
        model = ForwardReturnClassifier(
            random_state=random_state, C=C, max_iter=max_iter
        )
        model.fit(train.X, train.y)
        proba = model.proba_up(test.X)
        fold_auc = _safe_auc(test.y, proba)
        fold_aucs.append(fold_auc)
        last_model = model
        last_train = train
        last_test = test

    assert last_model is not None and last_train is not None and last_test is not None

    finite_aucs = [a for a in fold_aucs if not math.isnan(a)]
    if not finite_aucs:
        raise ValueError("walk-forward: todos os folds retornaram AUC NaN")

    mean_auc = float(np.mean(finite_aucs))
    std_auc = float(np.std(finite_aucs))
    min_auc = float(np.min(finite_aucs))

    deployable, deploy_reason = _compute_deployable_wf(
        mean_auc=mean_auc,
        std_auc=std_auc,
        fold_aucs=fold_aucs,
        min_deploy_auc=min_deploy_auc,
        max_std=max_std,
    )

    # Métricas do último fold, complementadas pelas do walk-forward.
    last_train_pred = last_model.predict(last_train.X)
    last_test_pred = last_model.predict(last_test.X)
    baseline_accuracy, baseline_auc = _baseline_metrics(
        last_train, last_test, random_state=random_state
    )

    test_metrics = {
        # `auc` = mean_auc para o `MLStrategy` consumir via `metrics.auc`.
        "auc": mean_auc,
        "accuracy": float(accuracy_score(last_test.y, last_test_pred)),
        "precision": float(
            precision_score(last_test.y, last_test_pred, zero_division=0)
        ),
        "recall": float(
            recall_score(last_test.y, last_test_pred, zero_division=0)
        ),
        "f1": float(f1_score(last_test.y, last_test_pred, zero_division=0)),
        "baseline_accuracy": baseline_accuracy,
        "baseline_auc": baseline_auc,
        "wf_mean_auc": mean_auc,
        "wf_std_auc": std_auc,
        "wf_min_auc": min_auc,
        "wf_num_folds": float(len(fold_aucs)),
    }

    metadata = ModelMetadata(
        version=make_version(symbol, dataset.horizon),
        symbol=symbol,
        horizon=dataset.horizon,
        feature_names=dataset.feature_names,
        trained_at=datetime.now(UTC).isoformat(),
        n_train=len(last_train),
        n_test=len(last_test),
        metrics=test_metrics,
        hyperparams={
            "C": C,
            "max_iter": max_iter,
            "random_state": random_state,
            "purge": dataset.horizon,
            "min_deploy_auc": min_deploy_auc,
            "max_std": max_std,
            "n_folds": n_folds,
            "min_train_size": min_train_size,
            "min_test_size": min_test_size,
            "label_min_return_pct": dataset.min_return_pct,
            "training_mode": "walk_forward",
        },
        baseline_accuracy=baseline_accuracy,
        baseline_auc=baseline_auc,
        n_purged=0,  # walk-forward purga dentro de cada fold
        deployable=deployable,
        deploy_reason=deploy_reason,
        walk_forward_aucs=[float(a) for a in fold_aucs],
    )

    return TrainingResult(
        model=last_model,
        metadata=metadata,
        train_accuracy=float(accuracy_score(last_train.y, last_train_pred)),
        test_accuracy=float(accuracy_score(last_test.y, last_test_pred)),
        test_metrics=test_metrics,
    )


def save_training_result(result: TrainingResult, model_dir: Path) -> Path:
    path = model_dir / f"{result.metadata.version}.joblib"
    result.model.save(path, result.metadata)
    return path