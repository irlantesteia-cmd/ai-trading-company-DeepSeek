"""Pipeline que adiciona features de ativos de referência (cross-asset).

Hipótese: em cripto, BTC lidera alts por 1–3 candles em 5m. Um modelo de
SOL/XRP que não "vê" o que BTC fez no último candle perde informação
preditiva. `CrossAssetPipeline` envelopa um `FeaturePipeline` base e
adiciona, para cada horizonte `h`, a feature `<ref>_return_<h>`.

O prefixo `<ref>` é o **base asset** em minúsculas (`BTCUSDT` → `btc`,
`XRPUSDT` → `xrp`), extraído removendo o quote currency do símbolo.
Isso mantém a nomenclatura alinhada com o resto do projeto (features
curtas como `return_1`, `rsi_14`) e evita nomes como `btcusdt_return_1`.

Alinhamento por `open_time`: os candles do ativo principal e do ref devem
ter o mesmo `interval` e o mesmo timestamp. Linhas sem correspondência
exata são descartadas (preserva causalidade).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from app.domain.models.market import Candle
from app.features.pipeline import FeatureMatrix, FeaturePipeline

# Quote currencies conhecidas. Nenhuma é sufixo de outra, então a ordem
# não importa. Se aparecer conflito futuro, ordenar da mais longa para a
# mais curta.
_QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def _symbol_prefix(symbol: str) -> str:
    """Extrai o prefixo curto (base asset) do símbolo: `BTCUSDT` → `btc`."""
    upper = symbol.upper()
    for quote in _QUOTE_SUFFIXES:
        if upper.endswith(quote) and len(upper) > len(quote):
            return upper[: -len(quote)].lower()
    return upper.lower()


class CrossAssetPipeline:
    def __init__(
        self,
        base: FeaturePipeline,
        *,
        ref_symbol: str,
        ref_horizons: list[int] | None = None,
    ) -> None:
        if not ref_symbol:
            raise ValueError("ref_symbol obrigatório")
        horizons = sorted(set(ref_horizons or [1]))
        if not horizons or any(h <= 0 for h in horizons):
            raise ValueError("ref_horizons deve conter inteiros > 0")
        self._base = base
        self._ref_symbol = ref_symbol
        self._horizons = horizons

    @property
    def base(self) -> FeaturePipeline:
        return self._base

    @property
    def ref_symbol(self) -> str:
        return self._ref_symbol

    @property
    def ref_horizons(self) -> list[int]:
        return list(self._horizons)

    @property
    def names(self) -> list[str]:
        prefix = _symbol_prefix(self._ref_symbol)
        return self._base.names + [
            f"{prefix}_return_{h}" for h in self._horizons
        ]

    def transform(
        self,
        candles: list[Candle],
        ref_candles: dict[str, list[Candle]] | None = None,
    ) -> FeatureMatrix:
        base_fm = self._base.transform(candles)
        if base_fm.values.shape[0] == 0:
            return self._empty_matrix()

        ref = (ref_candles or {}).get(self._ref_symbol)
        if not ref:
            raise ValueError(
                f"CrossAssetPipeline requer ref_candles['{self._ref_symbol}']"
            )

        max_h = max(self._horizons)
        if len(ref) < max_h + 1:
            return self._empty_matrix()

        ref_times = [c.open_time for c in ref]
        ref_closes = [float(c.close) for c in ref]
        ref_pos_by_time = {t: i for i, t in enumerate(ref_times)}

        kept_base_rows: list[NDArray[np.float64]] = []
        kept_extra: list[list[float]] = []
        kept_indices: list[int] = []
        kept_times: list[datetime] = []

        for k, orig_idx in enumerate(base_fm.indices):
            t = candles[orig_idx].open_time
            pos = ref_pos_by_time.get(t)
            if pos is None:
                continue
            row_extra: list[float] = []
            ok = True
            for h in self._horizons:
                prev_pos = pos - h
                if prev_pos < 0:
                    ok = False
                    break
                prev_close = ref_closes[prev_pos]
                curr_close = ref_closes[pos]
                if prev_close <= 0:
                    ok = False
                    break
                row_extra.append(curr_close / prev_close - 1.0)
            if not ok:
                continue
            kept_base_rows.append(base_fm.values[k])
            kept_extra.append(row_extra)
            kept_indices.append(orig_idx)
            kept_times.append(t)

        if not kept_base_rows:
            return self._empty_matrix()

        base_matrix = np.vstack(kept_base_rows)
        extra_matrix = np.asarray(kept_extra, dtype=np.float64)
        values = np.hstack([base_matrix, extra_matrix]).astype(np.float64)

        return FeatureMatrix(
            values=values,
            names=self.names,
            indices=kept_indices,
            times=kept_times,
        )

    def _empty_matrix(self) -> FeatureMatrix:
        return FeatureMatrix(
            values=np.zeros((0, len(self.names)), dtype=np.float64),
            names=self.names,
            indices=[],
            times=[],
        )