from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class ModelMetadata:
    version: str
    symbol: str
    horizon: int
    feature_names: list[str]
    trained_at: str
    n_train: int
    n_test: int
    metrics: dict[str, float] = field(default_factory=dict)
    hyperparams: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ForwardReturnClassifier:
    """Classificador binário: P(close[t+h] > close[t]).

    Pipeline: StandardScaler → LogisticRegression (class_weight=balanced).
    """

    def __init__(
        self,
        *,
        random_state: int = 42,
        C: float = 1.0,
        max_iter: int = 2000,
    ) -> None:
        self.random_state = random_state
        self.C = C
        self.max_iter = max_iter
        self._pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=C,
                        max_iter=max_iter,
                        class_weight="balanced",
                        random_state=random_state,
                    ),
                ),
            ]
        )
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def classes_(self) -> NDArray:
        return self._pipeline.named_steps["clf"].classes_

    def fit(
        self, X: NDArray[np.float64], y: NDArray[np.int64]
    ) -> ForwardReturnClassifier:
        if X.ndim != 2:
            raise ValueError(f"X deve ser 2D, got {X.ndim}")
        if len(np.unique(y)) < 2:
            raise ValueError(
                "y precisa ter pelo menos 2 classes distintas para treinar"
            )
        self._pipeline.fit(X, y)
        self._fitted = True
        return self

    def predict_proba(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        if not self._fitted:
            raise RuntimeError("modelo não treinado")
        return self._pipeline.predict_proba(X)

    def predict(self, X: NDArray[np.float64]) -> NDArray[np.int64]:
        if not self._fitted:
            raise RuntimeError("modelo não treinado")
        return self._pipeline.predict(X).astype(np.int64)

    def proba_up(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Probabilidade da classe 1 (alta). Alinha com `Signal.confidence`."""
        proba = self.predict_proba(X)
        idx = int(np.where(self.classes_ == 1)[0][0]) if 1 in self.classes_ else 0
        return proba[:, idx]

    # ------------------------------------------------------------- persistence
    def save(self, path: Path, metadata: ModelMetadata) -> None:
        if not self._fitted:
            raise RuntimeError("não é possível salvar modelo não treinado")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._pipeline, path)
        metadata_path = path.with_suffix(".json")
        import json

        metadata_path.write_text(
            json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> ForwardReturnClassifier:
        pipeline = joblib.load(path)
        obj = cls.__new__(cls)
        obj.random_state = 42
        obj.C = 1.0
        obj.max_iter = 2000
        obj._pipeline = pipeline
        obj._fitted = True
        return obj


def make_version(symbol: str, horizon: int) -> str:
    """Versionamento determinístico-ish por timestamp UTC."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{symbol}_h{horizon}_{ts}"