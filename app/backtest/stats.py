"""Estatística do laboratório: resumo de trades, bootstrap e walk-forward.

As médias são por trade, em % do notional de entrada, para poder juntar
símbolos e timeframes diferentes no mesmo agregado.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from app.backtest.types import BacktestTrade


class TradeStats(BaseModel):
    trades: int = 0
    win_rate: float = 0.0
    avg_gross_pct: float = 0.0
    avg_cost_pct: float = 0.0
    avg_net_pct: float = 0.0
    profit_factor: float = 0.0  # soma dos líquidos positivos / |soma dos negativos|


def summarize(trades: Sequence[BacktestTrade]) -> TradeStats:
    if not trades:
        return TradeStats()
    n = len(trades)
    net = [t.return_pct for t in trades]
    gross = [t.gross_return_pct for t in trades]
    gains = sum(x for x in net if x > 0)
    losses = -sum(x for x in net if x < 0)
    if losses > 0:
        pf = gains / losses
    else:
        pf = float("inf") if gains > 0 else 0.0
    avg_net = sum(net) / n
    avg_gross = sum(gross) / n
    return TradeStats(
        trades=n,
        win_rate=sum(1 for x in net if x > 0) / n,
        avg_gross_pct=avg_gross,
        avg_cost_pct=avg_gross - avg_net,
        avg_net_pct=avg_net,
        profit_factor=pf,
    )


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float]:
    """Intervalo de confiança (percentil) da média, reamostrando com reposição.
    Semente fixa: o mesmo conjunto de trades dá sempre o mesmo intervalo."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    tail = (1 - confidence) / 2
    lo = means[int(tail * resamples)]
    hi = means[min(resamples - 1, int((1 - tail) * resamples))]
    return (lo, hi)


class FoldStats(BaseModel):
    index: int
    start: datetime
    end: datetime
    stats: TradeStats


def walk_forward(
    trades: Sequence[BacktestTrade], *, start: datetime, end: datetime, folds: int = 5
) -> list[FoldStats]:
    """Divide [start, end) em `folds` blocos de mesma duração e resume os
    trades de cada bloco pela hora de entrada."""
    if folds < 1 or end <= start:
        raise ValueError("folds >= 1 e end > start")
    span = (end - start) / folds
    out: list[FoldStats] = []
    for k in range(folds):
        lo = start + span * k
        hi = end if k == folds - 1 else start + span * (k + 1)
        inside = [t for t in trades if lo <= t.entry_time < hi or (k == folds - 1 and t.entry_time == hi)]
        out.append(FoldStats(index=k + 1, start=lo, end=hi, stats=summarize(inside)))
    return out
