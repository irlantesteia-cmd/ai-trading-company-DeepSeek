from __future__ import annotations

from collections.abc import Sequence
from math import log

from app.core.indicators import atr, ema, rolling_std


def return_n(closes: Sequence[float], n: int) -> list[float | None]:
    """Retorno simples em `n` barras: close[i]/close[i-n] - 1."""
    if n <= 0:
        raise ValueError("n deve ser > 0")
    out: list[float | None] = [None] * len(closes)
    for i in range(n, len(closes)):
        prev = closes[i - n]
        if prev == 0:
            continue
        out[i] = closes[i] / prev - 1.0
    return out


def log_return(closes: Sequence[float], n: int = 1) -> list[float | None]:
    if n <= 0:
        raise ValueError("n deve ser > 0")
    out: list[float | None] = [None] * len(closes)
    for i in range(n, len(closes)):
        prev = closes[i - n]
        if prev <= 0 or closes[i] <= 0:
            continue
        out[i] = log(closes[i] / prev)
    return out


def volatility(closes: Sequence[float], period: int = 20) -> list[float | None]:
    """Desvio-padrão dos log-returns em janela `period`."""
    lr = log_return(closes, 1)
    # Substitui None por 0 para alimentar rolling_std; ainda produz None onde inválido.
    filled = [0.0 if v is None else v for v in lr]
    std = rolling_std(filled, period)
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if lr[i] is None or std[i] is None:
            continue
        out[i] = std[i]
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """RSI de Wilder (0..100). `None` durante o warmup."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains[i] = delta
        else:
            losses[i] = -delta

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period

    def _rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def volume_zscore(volumes: Sequence[float], period: int = 20) -> list[float | None]:
    """(v[i] - média(v, period)) / std(v, period)."""
    n = len(volumes)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    for i in range(period - 1, n):
        window = volumes[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        std = var**0.5
        out[i] = 0.0 if std == 0 else (volumes[i] - mean) / std
    return out


def ema_distance(closes: Sequence[float], period: int) -> list[float | None]:
    """(close[i] - EMA(period)[i]) / close[i]."""
    e = ema(closes, period)
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        ema_i = e[i]
        if ema_i is None or closes[i] == 0:
            continue
        out[i] = (closes[i] - ema_i) / closes[i]
    return out


def atr_normalized(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    """ATR(period) / close[i]."""
    a = atr(highs, lows, closes, period)
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        atr_i = a[i]
        if atr_i is None or closes[i] == 0:
            continue
        out[i] = atr_i / closes[i]
    return out


def high_low_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if closes[i] == 0:
            continue
        out[i] = (highs[i] - lows[i]) / closes[i]
    return out


def taker_buy_ratio(
    volumes: Sequence[float],
    taker_buys: Sequence[float | None],
) -> list[float | None]:
    """Fração do volume agressor (taker buy) no candle: taker_buy / volume.

    Causal no mesmo candle fechado. `None` se volume <= 0 ou taker ausente.
    Valores fora de [0, 1] (arredondamento da exchange) são clipados.
    """
    if len(volumes) != len(taker_buys):
        raise ValueError("volumes e taker_buys devem ter o mesmo comprimento")
    out: list[float | None] = [None] * len(volumes)
    for i, vol in enumerate(volumes):
        tb = taker_buys[i]
        if tb is None or vol <= 0:
            continue
        ratio = tb / vol
        if ratio < 0.0:
            out[i] = 0.0
        elif ratio > 1.0:
            out[i] = 1.0
        else:
            out[i] = ratio
    return out


def body_ratio(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float | None]:
    """(close - open) / (high - low). Doji → 0; fora de faixa → None."""
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        rng = highs[i] - lows[i]
        if rng == 0:
            out[i] = 0.0
        else:
            out[i] = (closes[i] - opens[i]) / rng
    return out