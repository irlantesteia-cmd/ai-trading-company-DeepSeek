from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from app.domain.models.market import Candle
from app.features.engineering import (
    atr_normalized,
    body_ratio,
    ema_distance,
    high_low_range,
    log_return,
    return_n,
    rsi,
    volatility,
    volume_zscore,
)

FeatureFn = Callable[[list[Candle]], list[float | None]]


@dataclass(frozen=True)
class FeatureMatrix:
    """Matriz alinhada de features.

    `values[j]` corresponde a `times[j]`, que corresponde ao candle em
    `indices[j]` da lista original.
    """

    values: NDArray[np.float64]  # shape (n_valid, n_features)
    names: list[str]
    indices: list[int]           # índices na lista de candles original
    times: list[datetime]        # open_time do candle correspondente


class FeaturePipeline:
    """Pipeline declarativo: lista de (nome, função de candle → série de features)."""

    def __init__(self, features: list[tuple[str, FeatureFn]]) -> None:
        if not features:
            raise ValueError("pipeline precisa de ao menos uma feature")
        self._features = features

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self._features]

    def transform(self, candles: list[Candle]) -> FeatureMatrix:
        if not candles:
            return FeatureMatrix(
                values=np.zeros((0, len(self._features)), dtype=np.float64),
                names=self.names,
                indices=[],
                times=[],
            )

        series_per_feature: list[list[float | None]] = [
            fn(candles) for _, fn in self._features
        ]
        n = len(candles)
        n_feat = len(self._features)

        rows: list[list[float]] = []
        indices: list[int] = []
        times: list[datetime] = []

        for i in range(n):
            row = [series_per_feature[k][i] for k in range(n_feat)]
            if any(v is None for v in row):
                continue
            rows.append([float(v) for v in row])  # type: ignore[arg-type]
            indices.append(i)
            times.append(candles[i].open_time)

        values = (
            np.asarray(rows, dtype=np.float64)
            if rows
            else np.zeros((0, n_feat), dtype=np.float64)
        )
        return FeatureMatrix(
            values=values,
            names=self.names,
            indices=indices,
            times=times,
        )


def default_pipeline(
    *,
    ema_fast: int = 9,
    ema_slow: int = 21,
    rsi_period: int = 14,
    atr_period: int = 14,
    vol_period: int = 20,
    volume_period: int = 20,
) -> FeaturePipeline:
    """Conjunto padrão de 13 features, todas causalmente válidas."""

    def _return_1(c: list[Candle]) -> list[float | None]:
        return return_n([float(x.close) for x in c], 1)

    def _return_3(c: list[Candle]) -> list[float | None]:
        return return_n([float(x.close) for x in c], 3)

    def _return_5(c: list[Candle]) -> list[float | None]:
        return return_n([float(x.close) for x in c], 5)

    def _return_10(c: list[Candle]) -> list[float | None]:
        return return_n([float(x.close) for x in c], 10)

    def _logret_1(c: list[Candle]) -> list[float | None]:
        return log_return([float(x.close) for x in c], 1)

    def _vol(c: list[Candle]) -> list[float | None]:
        return volatility([float(x.close) for x in c], vol_period)

    def _rsi(c: list[Candle]) -> list[float | None]:
        return rsi([float(x.close) for x in c], rsi_period)

    def _volz(c: list[Candle]) -> list[float | None]:
        return volume_zscore([float(x.volume) for x in c], volume_period)

    def _ema_fast_dist(c: list[Candle]) -> list[float | None]:
        return ema_distance([float(x.close) for x in c], ema_fast)

    def _ema_slow_dist(c: list[Candle]) -> list[float | None]:
        return ema_distance([float(x.close) for x in c], ema_slow)

    def _atr_norm(c: list[Candle]) -> list[float | None]:
        return atr_normalized(
            [float(x.high) for x in c],
            [float(x.low) for x in c],
            [float(x.close) for x in c],
            atr_period,
        )

    def _range(c: list[Candle]) -> list[float | None]:
        return high_low_range(
            [float(x.high) for x in c],
            [float(x.low) for x in c],
            [float(x.close) for x in c],
        )

    def _body(c: list[Candle]) -> list[float | None]:
        return body_ratio(
            [float(x.open) for x in c],
            [float(x.high) for x in c],
            [float(x.low) for x in c],
            [float(x.close) for x in c],
        )

    return FeaturePipeline(
        [
            ("return_1", _return_1),
            ("return_3", _return_3),
            ("return_5", _return_5),
            ("return_10", _return_10),
            ("log_return_1", _logret_1),
            (f"volatility_{vol_period}", _vol),
            (f"rsi_{rsi_period}", _rsi),
            (f"volume_zscore_{volume_period}", _volz),
            (f"ema_fast_dist_{ema_fast}", _ema_fast_dist),
            (f"ema_slow_dist_{ema_slow}", _ema_slow_dist),
            (f"atr_norm_{atr_period}", _atr_norm),
            ("high_low_range", _range),
            ("body_ratio", _body),
        ]
    )