"""Benchmarks do laboratório (Fase 4): o resultado de uma estratégia só tem
significado comparado com não operar, com comprar e segurar e com o acaso.

- **Comprar e segurar**: retorno e drawdown do ativo no mesmo período.
- **Timing aleatório** (teste placebo): para cada trade da estratégia,
  sorteia uma barra de entrada qualquer com o MESMO lado e a MESMA duração
  (entrada na abertura, saída no fechamento da última barra) e mede o retorno
  bruto. Repetido `replicates` vezes, dá a distribuição do que o acaso faz com
  o mesmo padrão de exposição. O p-valor é a fração de réplicas com média
  ≥ à da estratégia: alto = o sinal não escolhe momentos melhores que o acaso.

Aproximação declarada: a saída placebo usa o fechamento da última barra, não
o preço intrabarra de stop/alvo; o bruto da estratégia vem dos preços sem
derrapagem, então os dois são comparáveis em bruto.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from statistics import mean

from pydantic import BaseModel

from app.backtest.types import BacktestTrade
from app.domain.models.market import Candle


class BuyHold(BaseModel):
    return_pct: float
    max_drawdown_pct: float


class TimingTest(BaseModel):
    trades: int = 0
    replicates: int = 0
    strategy_gross_pct: float = 0.0   # bruto médio por trade da estratégia
    random_gross_pct: float = 0.0     # média das réplicas aleatórias
    random_ci95: tuple[float, float] = (0.0, 0.0)
    p_value: float = 1.0


def buy_and_hold(candles: Sequence[Candle]) -> BuyHold:
    """Compra na abertura do primeiro candle e segura até o último fechamento."""
    if not candles:
        return BuyHold(return_pct=0.0, max_drawdown_pct=0.0)
    entry = float(candles[0].open)
    peak, worst = entry, 0.0
    for c in candles:
        close = float(c.close)
        peak = max(peak, close)
        worst = max(worst, (peak - close) / peak * 100 if peak > 0 else 0.0)
    return BuyHold(return_pct=(float(candles[-1].close) / entry - 1) * 100, max_drawdown_pct=worst)


def random_timing_test(
    runs: Sequence[tuple[Sequence[Candle], Sequence[BacktestTrade]]],
    *,
    replicates: int = 200,
    seed: int = 42,
) -> TimingTest:
    """Compara o bruto médio da estratégia com entradas aleatórias de mesmo
    lado e duração, ativo a ativo (cada trade sorteia no seu próprio histórico)."""
    specs: list[tuple[list[float], list[float], int, int]] = []
    strategy: list[float] = []
    for candles, trades in runs:
        if not trades:
            continue
        opens = [float(c.open) for c in candles]
        closes = [float(c.close) for c in candles]
        for t in trades:
            held = max(1, t.bars_held)
            if held > len(candles):
                continue
            sign = 1 if t.direction.value == "LONG" else -1
            specs.append((opens, closes, held, sign))
            strategy.append(t.gross_return_pct)
    if not specs or replicates < 1:
        return TimingTest()

    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(replicates):
        total = 0.0
        for opens, closes, held, sign in specs:
            e = rng.randrange(0, len(opens) - held + 1)
            total += sign * (closes[e + held - 1] / opens[e] - 1) * 100
        means.append(total / len(specs))
    means.sort()
    observed = mean(strategy)
    at_least = sum(1 for m in means if m >= observed)
    return TimingTest(
        trades=len(specs),
        replicates=replicates,
        strategy_gross_pct=observed,
        random_gross_pct=mean(means),
        random_ci95=(means[int(0.025 * replicates)], means[min(replicates - 1, int(0.975 * replicates))]),
        # +1 no numerador e no denominador: nunca diz p = 0 com amostra finita.
        p_value=(at_least + 1) / (replicates + 1),
    )
