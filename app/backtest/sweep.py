"""Sensibilidade a parâmetros (Fase 4b): roda o laboratório numa grade de
parâmetros e mostra se o resultado se sustenta nos vizinhos da configuração
declarada ou depende de um valor exato (sinal de ajuste ao passado).

É exploratório: escolher o melhor ponto da grade é viés de seleção, então a
varredura não dá veredito — só o gate da configuração declarada dá.
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.backtest.lab import (
    CandleLoader,
    FundingLoader,
    LabConfig,
    _default_funding_loader,
    _default_loader,
    run_lab,
)
from app.domain.models.market import Candle


class SweepPoint(BaseModel):
    params: dict[str, float]
    declared: bool              # é a configuração declarada (parâmetros base)?
    trades: int
    net_pct: float
    gross_pct: float
    ci95_low: float
    positive_folds: int
    folds: int
    profit_factor: float
    max_drawdown_pct: float
    timing_p: float
    approved: bool


class SweepReport(BaseModel):
    generated_at: datetime
    config: LabConfig
    grid: dict[str, list[float]]
    points: list[SweepPoint]
    notes: list[str] = Field(default_factory=lambda: [
        (
            "Exploratório: escolher o melhor ponto da grade é viés de seleção; só a "
            "configuração declarada (★) recebe veredito do gate."
        ),
        "p do timing sem correção para múltiplos testes.",
    ])


def grid_points(grid: dict[str, list[float]]) -> list[dict[str, float]]:
    keys = sorted(grid)
    return [dict(zip(keys, values, strict=True)) for values in itertools.product(*(grid[k] for k in keys))]


def _memo(fn: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
    cache: dict[tuple, object] = {}

    async def wrapper(*args):
        if args not in cache:
            cache[args] = await fn(*args)
        return cache[args]

    return wrapper


async def run_sweep(
    config: LabConfig,
    grid: dict[str, list[float]],
    *,
    loader: CandleLoader | None = None,
    funding_loader: FundingLoader | None = None,
) -> SweepReport:
    """Uma execução do laboratório por ponto da grade, reaproveitando os dados
    carregados (cada ativo/timeframe é lido uma vez)."""
    if not grid:
        raise ValueError("grade vazia: use --sweep parametro=v1,v2")
    load: Callable[[str, str, float], Awaitable[list[Candle]]] = _memo(loader or _default_loader)
    load_fund = _memo(funding_loader or _default_funding_loader)
    points: list[SweepPoint] = []
    for combo in grid_points(grid):
        params = {**config.params, **combo}
        report = await run_lab(config.model_copy(update={"params": params}),
                               loader=load, funding_loader=load_fund)
        o = report.overall
        declared = all(v == config.params.get(k, _default(config, k)) for k, v in combo.items())
        points.append(SweepPoint(
            params=combo,
            declared=declared,
            trades=o.trades,
            net_pct=o.avg_net_pct,
            gross_pct=o.avg_gross_pct,
            ci95_low=report.ci95_net_pct[0],
            positive_folds=sum(1 for f in report.folds if f.stats.trades and f.stats.avg_net_pct > 0),
            folds=len(report.folds),
            profit_factor=o.profit_factor,
            max_drawdown_pct=report.max_drawdown_pct,
            timing_p=report.timing.p_value,
            approved=report.verdict.approved,
        ))
    return SweepReport(generated_at=datetime.now(UTC), config=config, grid=grid, points=points)


def _default(config: LabConfig, key: str) -> float | None:
    """Valor padrão do parâmetro na estratégia (a configuração declarada sem --param)."""
    import inspect

    from app.backtest.lab import STRATEGIES

    cls = STRATEGIES.get(config.strategy)
    if cls is None:
        return None
    param = inspect.signature(cls.__init__).parameters.get(key)
    if param is None or param.default is inspect.Parameter.empty:
        return None
    return float(param.default)


def to_markdown(r: SweepReport) -> str:
    pts = r.points
    n = len(pts)
    positive = sum(1 for p in pts if p.net_pct > 0)
    ci_ok = sum(1 for p in pts if p.ci95_low > 0)
    gross_ok = sum(1 for p in pts if p.gross_pct > 0)
    nets = [p.net_pct for p in pts]
    c = r.config
    lines = [
        f"# Sensibilidade: {c.strategy}",
        "",
        f"- Símbolos: {', '.join(c.symbols)} | Timeframes: {', '.join(c.intervals)} | {c.days:g} dias",
        f"- Grade: {', '.join(f'{k} = {v}' for k, v in sorted(r.grid.items()))} ({n} configurações)",
        (
            f"- Fixos: {c.params or 'padrão'} | stop móvel: {'sim' if c.trailing_stop else 'não'}"
            f" | funding: {'real' if c.funding else 'não'}"
        ),
        "",
        "## Resumo",
        "",
        (
            f"- Bruto positivo em **{gross_ok}/{n}**; líquido positivo em **{positive}/{n}**; "
            f"IC 95% inferior > 0 em **{ci_ok}/{n}**."
        ),
        f"- Líquido por trade de {min(nets):+.3f}% a {max(nets):+.3f}%." if nets else "- Sem pontos.",
        "",
        "## Grade",
        "",
        "| Parâmetros | Trades | Bruto/trade | Líquido/trade | IC 95% inf. | Blocos + | PF | DD máx. | Timing p | Gate |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in pts:
        label = ", ".join(f"{k}={v:g}" for k, v in sorted(p.params.items()))
        pf = "inf" if p.profit_factor == float("inf") else f"{p.profit_factor:.2f}"
        lines.append(
            f"| {'★ ' if p.declared else ''}{label} | {p.trades} | {p.gross_pct:+.3f}% | {p.net_pct:+.3f}% "
            f"| {p.ci95_low:+.3f}% | {p.positive_folds}/{p.folds} | {pf} | {p.max_drawdown_pct:.1f}% "
            f"| {p.timing_p:.3f} | {'✅' if p.approved else '❌'} |"
        )
    lines += ["", "## Observações", "", *[f"- {x}" for x in r.notes], ""]
    return "\n".join(lines)
