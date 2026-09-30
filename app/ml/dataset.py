from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from app.domain.models.market import Candle
from app.features.pipeline import FeaturePipeline


@dataclass(frozen=True)
class Dataset:
    """Dataset supervisionado.

    Invariantes:
        X[j] usa candles[0..indices[j]] (nada além).
        y[j] usa candles[indices[j] + horizon].close vs candles[indices[j]].close.
    """

    X: NDArray[np.float64]
    y: NDArray[np.int64]
    feature_names: list[str]
    times: list[datetime]
    indices: list[int]
    horizon: int

    def __len__(self) -> int:
        return int(self.X.shape[0])


def build_dataset(
    candles: list[Candle],
    *,
    pipeline: FeaturePipeline,
    horizon: int = 5,
) -> Dataset:
    if horizon <= 0:
        raise ValueError("horizon deve ser > 0")

    fm = pipeline.transform(candles)
    n = len(candles)

    X_rows: list[NDArray[np.float64]] = []
    y_rows: list[int] = []
    times: list[datetime] = []
    indices: list[int] = []

    for j, idx in enumerate(fm.indices):
        target = idx + horizon
        if target >= n:
            break
        fwd = float(candles[target].close) - float(candles[idx].close)
        label = 1 if fwd > 0 else 0
        X_rows.append(fm.values[j])
        y_rows.append(label)
        times.append(fm.times[j])
        indices.append(idx)

    if not X_rows:
        return Dataset(
            X=np.zeros((0, len(fm.names)), dtype=np.float64),
            y=np.zeros((0,), dtype=np.int64),
            feature_names=fm.names,
            times=[],
            indices=[],
            horizon=horizon,
        )

    return Dataset(
        X=np.vstack(X_rows).astype(np.float64),
        y=np.asarray(y_rows, dtype=np.int64),
        feature_names=fm.names,
        times=times,
        indices=indices,
        horizon=horizon,
    )


def temporal_split(
    dataset: Dataset,
    *,
    test_size: float = 0.2,
) -> tuple[Dataset, Dataset]:
    """Split temporal estrito: primeiros (1 - test_size) para treino, resto para teste.

    Sem shuffle — preserva ordem cronológica para evitar vazamento.
    """
    if not 0.0 < test_size < 1.0:
        raise ValueError("test_size deve estar em (0, 1)")
    n = len(dataset)
    if n < 2:
        raise ValueError(f"dataset muito pequeno para split: {n} amostras")

    split_at = int(n * (1.0 - test_size))
    split_at = max(1, min(n - 1, split_at))

    def _slice(lo: int, hi: int) -> Dataset:
        return Dataset(
            X=dataset.X[lo:hi],
            y=dataset.y[lo:hi],
            feature_names=dataset.feature_names,
            times=dataset.times[lo:hi],
            indices=dataset.indices[lo:hi],
            horizon=dataset.horizon,
        )

    return _slice(0, split_at), _slice(split_at, n)