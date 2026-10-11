"""Laboratório de carteira: roda o momentum entre ativos e aplica o gate.

A unidade estatística é o **período da carteira** (entre dois
rebalanceamentos), não a posição: as posições de um mesmo período andam
juntas, e contá-las como independentes estreitaria o IC artificialmente.
O critério de amostra do gate conta as posições, e um critério extra exige
um mínimo de períodos.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.backtest.data import load_candles, load_funding
from app.backtest.gate import GateCheck, GateCriteria, GateVerdict, evaluate_gate
from app.backtest.portfolio import (
    PortfolioCosts,
    PortfolioLeg,
    PortfolioPeriod,
    XSMomentumParams,
    run_xs_momentum,
)
from app.backtest.stats import FoldStats, TradeStats, bootstrap_mean_ci
from app.domain.models.market import Candle

PORTFOLIO_STRATEGIES = {"xs_momentum"}
INTERVAL = "1d"

LIMITATIONS = [
    (
        "Universo fixo de ativos listados hoje (viés de sobrevivência): ativos "
        "que sumiram no período não entram, o que tende a melhorar o resultado."
    ),
    (
        "Execução na abertura diária com custo fixo (corretagem + derrapagem) "
        "sobre o notional negociado; sem impacto de mercado."
    ),
    "Funding sobre o notional na abertura do dia de cada liquidação.",
    "Um único dia de rebalanceamento (ancorado na segunda-feira); outro dia pode dar outro resultado.",
    "Médias por período em % da equity no início do período.",
]

CandleLoader = Callable[[str, str, float], Awaitable[list[Candle]]]
FundingLoader = Callable[[str, float], Awaitable[list[tuple[int, float]]]]


class PortfolioLabConfig(BaseModel):
    strategy: str = "xs_momentum"
    params: XSMomentumParams = Field(default_factory=XSMomentumParams)
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    days: float = 180
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    funding: bool = False
    initial_capital: float = 3000.0
    folds: int = 5
    seed: int = 42
    min_periods: int = 100
    criteria: GateCriteria = Field(default_factory=GateCriteria)


class LegSummary(BaseModel):
    symbol: str
    longs: int
    shorts: int
    avg_long_pct: float   # retorno médio da posição comprada, em % do notional
    avg_short_pct: float  # idem vendida (positivo = a venda ganhou)


class PortfolioReport(BaseModel):
    generated_at: datetime
    config: PortfolioLabConfig
    period_start: datetime
    period_end: datetime
    overall: TradeStats  # por período
    positions: int
    avg_long_pct: float   # contribuição média do lado comprado por período
    avg_short_pct: float  # idem vendido
    avg_turnover: float
    ci95_net_pct: tuple[float, float]
    folds: list[FoldStats]
    max_drawdown_pct: float
    by_symbol: list[LegSummary]
    verdict: GateVerdict
    limitations: list[str] = Field(default_factory=lambda: list(LIMITATIONS))


def period_stats(periods: Sequence[PortfolioPeriod]) -> TradeStats:
    """Mesmo resumo do laboratório por trade, com o período como unidade."""
    if not periods:
        return TradeStats()
    n = len(periods)
    net = [x.net_pct for x in periods]
    gains = sum(v for v in net if v > 0)
    losses = -sum(v for v in net if v < 0)
    pf = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
    return TradeStats(
        trades=n,
        win_rate=sum(1 for v in net if v > 0) / n,
        avg_gross_pct=sum(x.gross_pct for x in periods) / n,
        avg_cost_pct=sum(x.cost_pct for x in periods) / n,
        avg_funding_pct=sum(x.funding_pct for x in periods) / n,
        avg_net_pct=sum(net) / n,
        profit_factor=pf,
    )


def period_folds(
    periods: Sequence[PortfolioPeriod], *, start: datetime, end: datetime, folds: int
) -> list[FoldStats]:
    """Blocos cronológicos de mesma duração, pelo início de cada período."""
    if folds < 1 or end <= start:
        raise ValueError("folds >= 1 e end > start")
    span = (end - start) / folds
    out: list[FoldStats] = []
    for k in range(folds):
        lo = start + span * k
        hi = end if k == folds - 1 else start + span * (k + 1)
        inside = [x for x in periods if lo <= x.start < hi or (k == folds - 1 and x.start == hi)]
        out.append(FoldStats(index=k + 1, start=lo, end=hi, stats=period_stats(inside)))
    return out


async def _default_loader(symbol: str, interval: str, days: float) -> list[Candle]:
    return await load_candles(symbol, interval, days=days)


async def _default_funding_loader(symbol: str, days: float) -> list[tuple[int, float]]:
    return await load_funding(symbol, days=days)


async def run_portfolio_lab(
    config: PortfolioLabConfig,
    *,
    loader: CandleLoader | None = None,
    funding_loader: FundingLoader | None = None,
) -> PortfolioReport:
    if config.strategy not in PORTFOLIO_STRATEGIES:
        raise ValueError(f"estratégia de carteira desconhecida: {config.strategy!r}")
    load = loader or _default_loader
    load_fund = funding_loader or _default_funding_loader
    candles = {s: await load(s, INTERVAL, config.days) for s in config.symbols}
    candles = {s: cs for s, cs in candles.items() if cs}
    if not candles:
        raise ValueError("nenhum candle carregado")
    funding = {s: await load_fund(s, config.days) for s in candles} if config.funding else None

    result = run_xs_momentum(
        candles,
        params=config.params,
        costs=PortfolioCosts(
            fee_bps=config.fee_bps,
            slippage_bps=config.slippage_bps,
            initial_capital=config.initial_capital,
        ),
        funding=funding,
    )
    periods = result.periods
    if not periods:
        raise ValueError("nenhum período de carteira: histórico curto para o lookback")

    period_start = min(cs[0].open_time for cs in candles.values())
    period_end = max(cs[-1].close_time for cs in candles.values())
    overall = period_stats(periods)
    ci = bootstrap_mean_ci([x.net_pct for x in periods], seed=config.seed)
    folds = period_folds(periods, start=period_start, end=period_end, folds=config.folds)
    n = len(periods)
    periods_check = GateCheck(
        name="periodos",
        passed=n >= config.min_periods,
        value=str(n),
        required=f">= {config.min_periods}",
    )
    return PortfolioReport(
        generated_at=datetime.now(UTC),
        config=config,
        period_start=period_start,
        period_end=period_end,
        overall=overall,
        positions=len(result.legs),
        avg_long_pct=sum(x.long_pct for x in periods) / n,
        avg_short_pct=sum(x.short_pct for x in periods) / n,
        avg_turnover=sum(x.turnover for x in periods) / n,
        ci95_net_pct=ci,
        folds=folds,
        max_drawdown_pct=result.max_drawdown_pct,
        by_symbol=_by_symbol(result.legs, list(candles)),
        verdict=evaluate_gate(
            overall=overall,
            ci=ci,
            folds=folds,
            max_drawdown_pct=result.max_drawdown_pct,
            criteria=config.criteria,
            sample_size=len(result.legs),
            extra_checks=[periods_check],
        ),
    )


def _by_symbol(legs: Sequence[PortfolioLeg], symbols: list[str]) -> list[LegSummary]:
    out: list[LegSummary] = []
    for s in symbols:
        longs = [x.return_pct for x in legs if x.symbol == s and x.side == "LONG"]
        shorts = [x.return_pct for x in legs if x.symbol == s and x.side == "SHORT"]
        out.append(
            LegSummary(
                symbol=s,
                longs=len(longs),
                shorts=len(shorts),
                avg_long_pct=sum(longs) / len(longs) if longs else 0.0,
                avg_short_pct=sum(shorts) / len(shorts) if shorts else 0.0,
            )
        )
    return out


# ------------------------------------------------------------------ relatório
def _row(label: str, s: TradeStats) -> str:
    pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    return (
        f"| {label} | {s.trades} | {100 * s.win_rate:.1f}% | {s.avg_gross_pct:+.3f}% "
        f"| {s.avg_cost_pct:.3f}% | {s.avg_funding_pct:+.3f}% | {s.avg_net_pct:+.3f}% | {pf} |"
    )


_HEADER = (
    "| | Períodos | Positivos | Bruto/período | Custo/período | Funding/período "
    "| Líquido/período | Profit factor |\n|---|---|---|---|---|---|---|---|"
)


def to_markdown(r: PortfolioReport) -> str:
    c, p = r.config, r.config.params
    lines = [
        f"# Laboratório: {c.strategy} — {'APROVADA' if r.verdict.approved else 'REPROVADA'}",
        "",
        f"- Período: {r.period_start:%Y-%m-%d} a {r.period_end:%Y-%m-%d} ({c.days:g} dias, candles {INTERVAL})",
        f"- Símbolos ({len(c.symbols)}): {', '.join(c.symbols)}",
        (
            f"- Carteira: ordena pelo retorno de {p.lookback_days} dias, compra {p.top_k} e vende "
            f"{p.top_k}, rebalanceia a cada {p.rebalance_days} dias, peso igual (bruto 1×, líquido 0)"
        ),
        (
            f"- Custos: corretagem {c.fee_bps} bps + derrapagem {c.slippage_bps} bps sobre o "
            f"negociado | funding: {'real' if c.funding else 'não'}"
        ),
        "",
        "## Veredito",
        "",
        "| Critério | Valor | Exigido | |",
        "|---|---|---|---|",
        *[f"| {ch.name} | {ch.value} | {ch.required} | {'✅' if ch.passed else '❌'} |" for ch in r.verdict.checks],
        "",
        "O critério `trades` conta as posições; IC, blocos e profit factor usam o retorno por período.",
        "",
        "## Geral",
        "",
        _HEADER,
        _row("Todos", r.overall),
        "",
        (
            f"IC 95% do líquido por período: **{r.ci95_net_pct[0]:+.4f}% a "
            f"{r.ci95_net_pct[1]:+.4f}%** | drawdown máx.: {r.max_drawdown_pct:.1f}% | "
            f"posições: {r.positions} | giro médio por rebalanceamento: {100 * r.avg_turnover:.0f}% da equity"
        ),
        "",
        "## Lado comprado × vendido (bruto, % da equity por período)",
        "",
        "| Comprado | Vendido |",
        "|---|---|",
        f"| {r.avg_long_pct:+.3f}% | {r.avg_short_pct:+.3f}% |",
        "",
        "## Walk-forward (blocos cronológicos)",
        "",
        _HEADER,
        *[_row(f"{f.index}: {f.start:%Y-%m-%d} a {f.end:%Y-%m-%d}", f.stats) for f in r.folds],
        "",
        "## Por símbolo (retorno médio da posição, % do notional)",
        "",
        "| Símbolo | Vezes comprado | Comprado | Vezes vendido | Vendido |",
        "|---|---|---|---|---|",
        *[
            f"| {x.symbol} | {x.longs} | {x.avg_long_pct:+.3f}% | {x.shorts} | {x.avg_short_pct:+.3f}% |"
            for x in r.by_symbol
        ],
        "",
        "## Limitações",
        "",
        *[f"- {x}" for x in r.limitations],
        "",
    ]
    return "\n".join(lines)
