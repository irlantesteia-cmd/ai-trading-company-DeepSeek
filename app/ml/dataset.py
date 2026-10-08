from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from app.domain.models.market import Candle
from app.features.pipeline import FeaturePipeline


@dataclass(frozen=True)
class Dataset:
    X: NDArray[np.float64]
    y: NDArray[np.int64]
    feature_names: list[str]
    times: list[datetime]
    indices: list[int]
    horizon: int
    min_return_pct: float = 0.0

    def __len__(self) -> int:
        return int(self.X.shape[0])


def build_dataset(
    candles: list[Candle],
    *,
    pipeline: FeaturePipeline,
    horizon: int = 5,
    min_return_pct: float = 0.0,
    ref_candles: dict[str, list[Candle]] | None = None,
) -> Dataset:
    """Constrói X/y a partir dos candles.

    `ref_candles` (opcional): candles de ativos de referência para pipelines
    cross-asset. Ignorado por `FeaturePipeline` puro.
    """
    if horizon <= 0:
        raise ValueError("horizon deve ser > 0")
    if min_return_pct < 0:
        raise ValueError("min_return_pct deve ser >= 0")

    fm = pipeline.transform(candles, ref_candles=ref_candles)
    n = len(candles)

    X_rows: list[NDArray[np.float64]] = []
    y_rows: list[int] = []
    times: list[datetime] = []
    indices: list[int] = []

    for j, idx in enumerate(fm.indices):
        target = idx + horizon
        if target >= n:
            break
        entry_close = float(candles[idx].close)
        exit_close = float(candles[target].close)
        fwd = exit_close - entry_close
        if min_return_pct > 0 and entry_close > 0:
            fwd_pct = fwd / entry_close
            if abs(fwd_pct) < min_return_pct:
                continue
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
            min_return_pct=min_return_pct,
        )

    return Dataset(
        X=np.vstack(X_rows).astype(np.float64),
        y=np.asarray(y_rows, dtype=np.int64),
        feature_names=fm.names,
        times=times,
        indices=indices,
        horizon=horizon,
        min_return_pct=min_return_pct,
    )


def temporal_split(
    dataset: Dataset,
    *,
    test_size: float = 0.2,
    purge: int = 0,
) -> tuple[Dataset, Dataset]:
    if not 0.0 < test_size < 1.0:
        raise ValueError("test_size deve estar em (0, 1)")
    if purge < 0:
        raise ValueError("purge deve ser >= 0")
    n = len(dataset)
    if n < 2:
        raise ValueError(f"dataset muito pequeno para split: {n} amostras")

    split_at = int(n * (1.0 - test_size))
    split_at = max(1, min(n - 1, split_at))

    train_end = split_at - purge
    if train_end < 1:
        raise ValueError(
            f"purge={purge} elimina todo o conjunto de treino "
            f"(split_at={split_at}, n={n})"
        )

    def _slice(lo: int, hi: int) -> Dataset:
        return Dataset(
            X=dataset.X[lo:hi],
            y=dataset.y[lo:hi],
            feature_names=dataset.feature_names,
            times=dataset.times[lo:hi],
            indices=dataset.indices[lo:hi],
            horizon=dataset.horizon,
            min_return_pct=dataset.min_return_pct,
        )

    return _slice(0, train_end), _slice(split_at, n)


def walk_forward_splits(
    dataset: Dataset,
    *,
    n_folds: int = 5,
    min_train_size: int = 100,
    min_test_size: int = 20,
) -> list[tuple[Dataset, Dataset]]:
    if n_folds < 2:
        raise ValueError("n_folds deve ser >= 2")
    if min_train_size < 1:
        raise ValueError("min_train_size deve ser >= 1")
    if min_test_size < 1:
        raise ValueError("min_test_size deve ser >= 1")

    n = len(dataset)
    purge = dataset.horizon

    required = min_train_size + n_folds * min_test_size
    if n < required:
        raise ValueError(
            f"dataset muito pequeno para walk-forward: n={n} < "
            f"min_train_size({min_train_size}) + "
            f"n_folds({n_folds}) * min_test_size({min_test_size}) = {required}"
        )

    test_total = n - min_train_size
    test_fold_size = test_total // n_folds

    def _slice(lo: int, hi: int) -> Dataset:
        return Dataset(
            X=dataset.X[lo:hi],
            y=dataset.y[lo:hi],
            feature_names=dataset.feature_names,
            times=dataset.times[lo:hi],
            indices=dataset.indices[lo:hi],
            horizon=dataset.horizon,
            min_return_pct=dataset.min_return_pct,
        )

    folds: list[tuple[Dataset, Dataset]] = []
    for i in range(n_folds):
        test_start = min_train_size + i * test_fold_size
        test_end = n if i == n_folds - 1 else min_train_size + (i + 1) * test_fold_size

        train_end = test_start - purge
        if train_end < 1:
            raise ValueError(
                f"fold {i}: purge={purge} elimina todo o treino "
                f"(test_start={test_start})"
            )
        folds.append((_slice(0, train_end), _slice(test_start, test_end)))

    return folds