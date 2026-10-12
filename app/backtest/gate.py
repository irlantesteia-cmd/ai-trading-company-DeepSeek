"""Gate de promoção: critérios objetivos para uma estratégia ir ao vivo.

Só é APROVADA se passar em todos. Os limites são conservadores de propósito
(decisão do usuário, 2026-10-10).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from app.backtest.stats import FoldStats, TradeStats


class GateCriteria(BaseModel):
    min_trades: int = 300
    min_ci_lower_pct: float = 0.0       # limite inferior do IC 95% do líquido/trade > 0
    min_positive_folds: int = 4         # de `folds` blocos
    min_profit_factor: float = 1.1
    max_drawdown_pct: float = 20.0
    # Fase 4: o bruto precisa superar entradas aleatórias de mesmo lado e duração.
    max_random_p_value: float = 0.05


class GateCheck(BaseModel):
    name: str
    passed: bool
    value: str
    required: str


class GateVerdict(BaseModel):
    approved: bool
    checks: list[GateCheck]


def evaluate_gate(
    *,
    overall: TradeStats,
    ci: tuple[float, float],
    folds: Sequence[FoldStats],
    max_drawdown_pct: float,
    criteria: GateCriteria | None = None,
    sample_size: int | None = None,
    extra_checks: Sequence[GateCheck] = (),
) -> GateVerdict:
    """`sample_size` substitui `overall.trades` no critério de amostra (ex.:
    na carteira, `overall` é por período e a amostra conta as posições)."""
    c = criteria or GateCriteria()
    samples = overall.trades if sample_size is None else sample_size
    positive = sum(1 for f in folds if f.stats.trades > 0 and f.stats.avg_net_pct > 0)
    checks = [
        GateCheck(
            name="trades",
            passed=samples >= c.min_trades,
            value=str(samples),
            required=f">= {c.min_trades}",
        ),
        GateCheck(
            name="ic95_limite_inferior",
            passed=ci[0] > c.min_ci_lower_pct,
            value=f"{ci[0]:+.4f}%",
            required=f"> {c.min_ci_lower_pct:+.4f}%",
        ),
        GateCheck(
            name="blocos_positivos",
            passed=positive >= c.min_positive_folds,
            value=f"{positive}/{len(folds)}",
            required=f">= {c.min_positive_folds}/{len(folds)}",
        ),
        GateCheck(
            name="profit_factor",
            passed=overall.profit_factor > c.min_profit_factor,
            value=f"{overall.profit_factor:.3f}",
            required=f"> {c.min_profit_factor}",
        ),
        GateCheck(
            name="drawdown_max",
            passed=max_drawdown_pct <= c.max_drawdown_pct,
            value=f"{max_drawdown_pct:.1f}%",
            required=f"<= {c.max_drawdown_pct}%",
        ),
        *extra_checks,
    ]
    return GateVerdict(approved=all(ch.passed for ch in checks), checks=checks)
