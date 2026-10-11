"""Classificador de regime de mercado, causal (só usa dados até a barra).

Por barra, dois rótulos:
- tendência: UP / DOWN / SIDEWAYS, pela inclinação de uma EMA longa nas
  últimas `slope_bars` barras + força da tendência (ADX >= `adx_threshold`);
- volatilidade: LOW / NORMAL / HIGH, pelo ATR% da barra comparado à mediana
  do ATR% nas últimas `vol_lookback` barras.

`UNKNOWN` enquanto não há histórico suficiente. Como nenhum rótulo depende
de barras futuras, o mesmo classificador pode virar filtro de estratégia.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from app.core.indicators import adx, atr, ema
from app.domain.models.market import Candle

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeParams:
    ema_period: int = 200
    slope_bars: int = 20
    adx_period: int = 14
    adx_threshold: float = 25.0
    atr_period: int = 14
    vol_lookback: int = 500
    low_vol_ratio: float = 0.75   # ATR% < 0,75 × mediana → LOW
    high_vol_ratio: float = 1.25  # ATR% > 1,25 × mediana → HIGH


def classify_regimes(
    candles: list[Candle], params: RegimeParams | None = None
) -> list[tuple[str, str]]:
    """Rótulos (tendência, volatilidade) para cada barra."""
    p = params or RegimeParams()
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    ema_vals = ema(closes, p.ema_period)
    adx_vals = adx(highs, lows, closes, p.adx_period)
    atr_vals = atr(highs, lows, closes, p.atr_period)

    labels: list[tuple[str, str]] = []
    window: list[float] = []  # ATR% das últimas `vol_lookback` barras, ordenado
    history: list[float] = []  # mesma janela na ordem de chegada
    for i in range(len(candles)):
        labels.append((_trend(i, ema_vals, adx_vals, p), _vol(i, closes, atr_vals, window, history, p)))
    return labels


def _trend(i: int, ema_vals, adx_vals, p: RegimeParams) -> str:
    j = i - p.slope_bars
    now, before, strength = ema_vals[i], (ema_vals[j] if j >= 0 else None), adx_vals[i]
    if now is None or before is None or strength is None:
        return UNKNOWN
    if strength < p.adx_threshold:
        return "SIDEWAYS"
    return "UP" if now > before else "DOWN"


def _vol(i: int, closes, atr_vals, window: list[float], history: list[float], p: RegimeParams) -> str:
    value = atr_vals[i]
    if value is None or closes[i] <= 0:
        return UNKNOWN
    pct = value / closes[i]
    label = UNKNOWN
    if len(window) >= p.vol_lookback // 2:  # mediana só com histórico mínimo
        median = window[len(window) // 2]
        if pct < p.low_vol_ratio * median:
            label = "LOW"
        elif pct > p.high_vol_ratio * median:
            label = "HIGH"
        else:
            label = "NORMAL"
    # A barra atual entra na janela só depois de rotulada (causal).
    bisect.insort(window, pct)
    history.append(pct)
    if len(history) > p.vol_lookback:
        old = history.pop(0)
        window.pop(bisect.bisect_left(window, old))
    return label
