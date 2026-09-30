from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.ml.dataset import Dataset, temporal_split
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


def train_classifier(
    dataset: Dataset,
    *,
    symbol: str,
    test_size: float = 0.2,
    random_state: int = 42,
    C: float = 1.0,
    max_iter: int = 2000,
) -> TrainingResult:
    if len(dataset) < 10:
        raise ValueError(f"dataset muito pequeno: {len(dataset)} amostras")

    train, test = temporal_split(dataset, test_size=test_size)

    model = ForwardReturnClassifier(
        random_state=random_state, C=C, max_iter=max_iter
    )
    model.fit(train.X, train.y)

    train_pred = model.predict(train.X)
    test_pred = model.predict(test.X)
    test_proba = model.proba_up(test.X)

    test_metrics = {
        "accuracy": float(accuracy_score(test.y, test_pred)),
        "precision": float(precision_score(test.y, test_pred, zero_division=0)),
        "recall": float(recall_score(test.y, test_pred, zero_division=0)),
        "f1": float(f1_score(test.y, test_pred, zero_division=0)),
        "auc": _safe_auc(test.y, test_proba),
    }

    metadata = ModelMetadata(
        version=make_version(symbol, dataset.horizon),
        symbol=symbol,
        horizon=dataset.horizon,
        feature_names=dataset.feature_names,
        trained_at=__import__("datetime").datetime.now(
            __import__("datetime").UTC
        ).isoformat(),
        n_train=len(train),
        n_test=len(test),
        metrics=test_metrics,
        hyperparams={
            "C": C,
            "max_iter": max_iter,
            "random_state": random_state,
            "test_size": test_size,
        },
    )

    return TrainingResult(
        model=model,
        metadata=metadata,
        train_accuracy=float(accuracy_score(train.y, train_pred)),
        test_accuracy=float(accuracy_score(test.y, test_pred)),
        test_metrics=test_metrics,
    )


def save_training_result(result: TrainingResult, model_dir: Path) -> Path:
    path = model_dir / f"{result.metadata.version}.joblib"
    result.model.save(path, result.metadata)
    return path