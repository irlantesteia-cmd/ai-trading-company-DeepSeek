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
    return_n,
    rsi,
    volatility,
    volume_zscore,
)

FeatureFn = Callable[[list[Candle]], list[float | None]]


@dataclass(frozen=True)
class FeatureMatrix:
    values: NDArray[np.float64]
    names: list[str]
    indices: list[int]
    times: list[datetime]


class FeaturePipeline:
    """Pipeline declarativo: lista de (nome, função de candle → série de features)."""

    def __init__(self, features: list[tuple[str, FeatureFn]]) -> None:
        if not features:
            raise ValueError("pipeline precisa de ao menos uma feature")
        self._features = features

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self._features]

    def transform(
        self,
        candles: list[Candle],
        ref_candles: dict[str, list[Candle]] | None = None,
    ) -> FeatureMatrix:
        """Transforma candles em matriz de features.

        `ref_candles` é aceito para compatibilidade com `CrossAssetPipeline`,
        mas ignorado aqui (features são puramente intra-candle).
        """
        del ref_candles
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
    ema_fast: int | None = None,
    ema_slow: int = 21,
    rsi_period: int = 14,
    atr_period: int = 14,
    vol_period: int = 20,
    volume_period: int = 20,
) -> FeaturePipeline:
    """Conjunto padrão de 8 features, todas causalmente válidas."""
    # `ema_fast` aceito mas ignorado desde ML-3a (redundante com ema_slow).
    del ema_fast

    def _return_1(c: list[Candle]) -> list[float | None]:
        return return_n([float(x.close) for x in c], 1)

    def _return_10(c: list[Candle]) -> list[float | None]:
        return return_n([float(x.close) for x in c], 10)

    def _vol(c: list[Candle]) -> list[float | None]:
        return volatility([float(x.close) for x in c], vol_period)

    def _rsi(c: list[Candle]) -> list[float | None]:
        return rsi([float(x.close) for x in c], rsi_period)

    def _volz(c: list[Candle]) -> list[float | None]:
        return volume_zscore([float(x.volume) for x in c], volume_period)

    def _ema_slow_dist(c: list[Candle]) -> list[float | None]:
        return ema_distance([float(x.close) for x in c], ema_slow)

    def _atr_norm(c: list[Candle]) -> list[float | None]:
        return atr_normalized(
            [float(x.high) for x in c],
            [float(x.low) for x in c],
            [float(x.close) for x in c],
            atr_period,
        )

    def _body(c: list[Candle]) -> list[float | None]:
        return body_ratio(
            [float(x.open) for x in c],
            [float(x.high) for x in c],
            [float(x.low) for x in c],
            [float(x.close) for x in c],
        )

    features: list[tuple[str, FeatureFn]] = [
        ("return_1", _return_1),
        ("return_10", _return_10),
        (f"volatility_{vol_period}", _vol),
        (f"rsi_{rsi_period}", _rsi),
        (f"volume_zscore_{volume_period}", _volz),
        (f"ema_slow_dist_{ema_slow}", _ema_slow_dist),
        (f"atr_norm_{atr_period}", _atr_norm),
        ("body_ratio", _body),
    ]
    return FeaturePipeline(features)
