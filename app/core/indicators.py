from __future__ import annotations

from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Simple Moving Average — mesmo tamanho da entrada, `None` onde indisponível."""
    if period <= 0:
        raise ValueError("period deve ser > 0")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    window_sum = sum(values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """EMA com seed = SMA das primeiras `period` amostras (padrão Wilder)."""
    if period <= 0:
        raise ValueError("period deve ser > 0")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rolling_std(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period deve ser > 0")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out[i] = var**0.5
    return out


def zscore(values: Sequence[float], period: int) -> list[float | None]:
    means = sma(values, period)
    stds = rolling_std(values, period)
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        m, s = means[i], stds[i]
        if m is None or s is None or s == 0:
            continue
        out[i] = (values[i] - m) / s
    return out


def true_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float]:
    n = len(closes)
    out = [0.0] * n
    if n == 0:
        return out
    out[0] = highs[0] - lows[0]
    for i in range(1, n):
        out[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return out


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> list[float | None]:
    return ema(true_range(highs, lows, closes), period)


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> list[float | None]:
    """ADX clássico (Wilder), com suavização por EMA.

    Convenção: quando pdi = mdi = 0 (mercado sem movimento direcional),
    DX = 0 (não é "indefinido"). Isso permite que o ADX aqueça mesmo em
    séries planas — sem isso, estratégias que filtram por ADX nunca
    geram sinais em janelas iniciais de mercado lateral.
    """
    n = len(closes)
    if n < period + 1:
        return [None] * n

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    tr = true_range(highs, lows, closes)
    atr_vals = ema(tr, period)
    plus_sm = ema(plus_dm, period)
    minus_sm = ema(minus_dm, period)

    dx: list[float | None] = [None] * n
    for i in range(n):
        a = atr_vals[i]
        ps = plus_sm[i]
        ms = minus_sm[i]
        if a is None or a == 0 or ps is None or ms is None:
            continue
        pdi = 100.0 * ps / a
        mdi = 100.0 * ms / a
        denom = pdi + mdi
        # Sem movimento direcional → DX = 0 (mercado lateral puro).
        dx[i] = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0

    valid = [(i, v) for i, v in enumerate(dx) if v is not None]
    out: list[float | None] = [None] * n
    if len(valid) < period:
        return out

    k = 2.0 / (period + 1)
    seed = sum(v for _, v in valid[:period]) / period
    seed_idx = valid[period - 1][0]
    out[seed_idx] = seed
    prev = seed
    for i, v in valid[period:]:
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out