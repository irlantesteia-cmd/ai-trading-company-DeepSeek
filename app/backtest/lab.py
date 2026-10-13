"""Laboratório de estratégias: roda uma estratégia em símbolos × timeframes,
junta os trades e devolve estatística, walk-forward, regimes e o veredito
do gate de promoção.

Limitação (declarada no relatório): cada símbolo é simulado isolado; as
regras de portfólio do bot ao vivo (máx. posições, grupos correlacionados,
trava de trading) não entram, então o ao vivo faz menos trades.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.backtest.benchmarks import BuyHold, TimingTest, buy_and_hold, random_timing_test
from app.backtest.data import load_candles, load_funding
from app.backtest.engine import BacktestEngine
from app.backtest.gate import GateCheck, GateCriteria, GateVerdict, evaluate_gate
from app.backtest.regime import classify_regimes
from app.backtest.registry import bonferroni
from app.backtest.stats import FoldStats, TradeStats, bootstrap_mean_ci, summarize, walk_forward
from app.backtest.types import BacktestConfig, BacktestTrade
from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.strategies import MeanReversionStrategy, MomentumStrategy
from app.strategies.base import Strategy
from app.strategies.trend_breakout import TrendBreakoutStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "trend_breakout": TrendBreakoutStrategy,
}

LIMITATIONS = [
    (
        "Cada símbolo é simulado isolado: limites de portfólio do bot ao vivo "
        "(máx. posições, grupos correlacionados, trava de trading) não entram."
    ),
    "Stop e alvo na mesma barra contam como stop (pessimista).",
    "Médias por trade em % do notional; o drawdown é o pior entre as execuções.",
    (
        "Funding (quando ligado) usa as taxas reais liquidadas durante o trade "
        "sobre o notional de entrada; o funding real usa o notional no momento."
    ),
    (
        "Timing aleatório: a saída placebo usa o fechamento da última barra, não o "
        "preço intrabarra de stop/alvo; compara bruto com bruto."
    ),
]

CandleLoader = Callable[[str, str, float], Awaitable[list[Candle]]]
FundingLoader = Callable[[str, float], Awaitable[list[tuple[int, float]]]]


class LabConfig(BaseModel):
    strategy: str = "momentum"
    params: dict[str, float] = Field(default_factory=dict)
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    intervals: list[str] = Field(default_factory=lambda: ["5m"])
    days: float = 180
    entry_fee_bps: float = 5.0
    exit_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    hold_bars: int = 0
    trailing_stop: bool = False
    funding: bool = False
    max_position_notional: float | None = 2000.0
    initial_capital: float = 3000.0
    risk_per_trade_pct: float = 0.01
    folds: int = 5
    seed: int = 42
    random_replicates: int = 200
    # Hipóteses declaradas já testadas, incluindo esta (correção de Bonferroni
    # do p do timing aleatório). 1 = sem correção.
    tested_hypotheses: int = 1
    # Fim do período (exclusivo). None = agora. No passado, delimita um teste
    # fora da amostra: o período vai de `until - days` a `until`.
    until: datetime | None = None
    # Regimes de volatilidade em que nenhuma posição é aberta (ex.: ["HIGH"]).
    skip_vol: list[str] = Field(default_factory=list)
    criteria: GateCriteria = Field(default_factory=GateCriteria)


class RunSummary(BaseModel):
    symbol: str
    interval: str
    candles: int
    max_drawdown_pct: float
    ruined: bool = False  # capital simulado zerou: entradas pararam ali
    stats: TradeStats
    total_return_pct: float = 0.0
    exposure_pct: float = 0.0
    max_dd_duration_days: float = 0.0
    buy_hold: BuyHold | None = None


class LabReport(BaseModel):
    generated_at: datetime
    config: LabConfig
    period_start: datetime
    period_end: datetime
    runs: list[RunSummary]
    overall: TradeStats
    ci95_net_pct: tuple[float, float]
    folds: list[FoldStats]
    by_trend: dict[str, TradeStats]
    by_volatility: dict[str, TradeStats]
    max_drawdown_pct: float
    verdict: GateVerdict
    timing: TimingTest = Field(default_factory=TimingTest)
    timing_p_adjusted: float = 1.0
    limitations: list[str] = Field(default_factory=lambda: list(LIMITATIONS))


def build_strategy(name: str, params: dict[str, float]) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"estratégia desconhecida: {name!r} (opções: {sorted(STRATEGIES)})")
    cls = STRATEGIES[name]
    accepted = set(inspect.signature(cls.__init__).parameters) - {"self"}
    unknown = set(params) - accepted
    if unknown:
        raise ValueError(f"parâmetros inválidos para {name}: {sorted(unknown)} (aceitos: {sorted(accepted)})")
    # Inteiros continuam inteiros (períodos), o resto float.
    kwargs = {k: int(v) if float(v).is_integer() and k.endswith("period") else v for k, v in params.items()}
    return cls(**kwargs)


async def _default_loader(symbol: str, interval: str, days: float) -> list[Candle]:
    return await load_candles(symbol, interval, days=days)


async def _default_funding_loader(symbol: str, days: float) -> list[tuple[int, float]]:
    return await load_funding(symbol, days=days)


def _loaders_until(until: datetime) -> tuple[CandleLoader, FundingLoader]:
    async def candles(symbol: str, interval: str, days: float) -> list[Candle]:
        return await load_candles(symbol, interval, days=days, now=until)

    async def funding(symbol: str, days: float) -> list[tuple[int, float]]:
        return await load_funding(symbol, days=days, now=until)

    return candles, funding


async def run_lab(
    config: LabConfig,
    *,
    loader: CandleLoader | None = None,
    funding_loader: FundingLoader | None = None,
) -> LabReport:
    default_load, default_fund = (
        _loaders_until(config.until) if config.until is not None
        else (_default_loader, _default_funding_loader)
    )
    load = loader or default_load
    load_fund = funding_loader or default_fund
    funding_cache: dict[str, list[tuple[int, float]]] = {}
    engine = BacktestEngine(
        BacktestConfig(
            initial_capital=Decimal(str(config.initial_capital)),
            risk_per_trade_pct=config.risk_per_trade_pct,
            entry_fee_bps=config.entry_fee_bps,
            exit_fee_bps=config.exit_fee_bps,
            slippage_bps=config.slippage_bps,
            max_holding_bars=config.hold_bars,
            trailing_stop=config.trailing_stop,
            skip_entry_vol=frozenset(config.skip_vol),
            max_position_notional=(
                Decimal(str(config.max_position_notional))
                if config.max_position_notional is not None
                else None
            ),
        )
    )
    runs: list[RunSummary] = []
    trades: list[BacktestTrade] = []
    timing_runs: list[tuple[list[Candle], list[BacktestTrade]]] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    for interval in config.intervals:
        for symbol in config.symbols:
            candles = await load(symbol, interval, config.days)
            if not candles:
                continue
            strategy = build_strategy(config.strategy, config.params)
            funding = None
            if config.funding:
                if symbol not in funding_cache:
                    funding_cache[symbol] = await load_fund(symbol, config.days)
                funding = funding_cache[symbol]
            result = engine.run(
                symbol=symbol,
                interval=interval,
                market_type=MarketType.FUTURES,
                candles=candles,
                strategy=strategy,
                regimes=classify_regimes(candles),
                funding_rates=funding,
            )
            trades.extend(result.trades)
            timing_runs.append((candles, result.trades))
            starts.append(candles[0].open_time)
            ends.append(candles[-1].close_time)
            runs.append(
                RunSummary(
                    symbol=symbol,
                    interval=interval,
                    candles=len(candles),
                    max_drawdown_pct=result.metrics.get("max_drawdown_pct", 0.0),
                    ruined=bool(result.metrics.get("ruined")),
                    stats=summarize(result.trades),
                    total_return_pct=result.total_return_pct,
                    exposure_pct=result.metrics.get("exposure_pct", 0.0),
                    max_dd_duration_days=result.metrics.get("max_dd_duration_days", 0.0),
                    buy_hold=buy_and_hold(candles),
                )
            )
    if not runs:
        raise ValueError("nenhum candle carregado")

    period_start, period_end = min(starts), max(ends)
    overall = summarize(trades)
    ci = bootstrap_mean_ci([t.return_pct for t in trades], seed=config.seed)
    folds = walk_forward(trades, start=period_start, end=period_end, folds=config.folds)
    max_dd = max(r.max_drawdown_pct for r in runs)
    timing = random_timing_test(timing_runs, replicates=config.random_replicates, seed=config.seed)
    n_tests = max(1, config.tested_hypotheses)
    p_adjusted = bonferroni(timing.p_value, n_tests)
    timing_check = GateCheck(
        name="timing_vs_aleatorio",
        passed=timing.trades > 0 and p_adjusted <= config.criteria.max_random_p_value,
        value=(
            f"p={timing.p_value:.3f}" if n_tests == 1
            else f"p={timing.p_value:.3f} × {n_tests} hipóteses = {p_adjusted:.3f}"
        ),
        required=f"<= {config.criteria.max_random_p_value}",
    )
    return LabReport(
        generated_at=datetime.now(UTC),
        config=config,
        period_start=period_start,
        period_end=period_end,
        runs=runs,
        overall=overall,
        ci95_net_pct=ci,
        folds=folds,
        by_trend=_group(trades, lambda t: t.regime_trend),
        by_volatility=_group(trades, lambda t: t.regime_vol),
        max_drawdown_pct=max_dd,
        verdict=evaluate_gate(
            overall=overall, ci=ci, folds=folds, max_drawdown_pct=max_dd,
            criteria=config.criteria, extra_checks=[timing_check],
        ),
        timing=timing,
        timing_p_adjusted=p_adjusted,
    )


def _group(trades: list[BacktestTrade], key) -> dict[str, TradeStats]:
    buckets: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        buckets.setdefault(key(t) or "UNKNOWN", []).append(t)
    return {k: summarize(v) for k, v in sorted(buckets.items())}


# ------------------------------------------------------------------ relatório
def _benchmarks(r: LabReport) -> list[str]:
    t, o = r.timing, r.overall
    holds = [x.buy_hold for x in r.runs if x.buy_hold is not None]
    bh = sum(h.return_pct for h in holds) / len(holds) if holds else 0.0
    bh_dd = sum(h.max_drawdown_pct for h in holds) / len(holds) if holds else 0.0
    strat = sum(x.total_return_pct for x in r.runs) / len(r.runs) if r.runs else 0.0
    return [
        "## Benchmarks",
        "",
        "| Referência | Resultado |",
        "|---|---|",
        f"| Estratégia | líquido {o.avg_net_pct:+.3f}%/trade; retorno médio por execução {strat:+.1f}% |",
        "| Não operar | 0% (nenhum custo, nenhum risco) |",
        f"| Comprar e segurar (média dos ativos) | {bh:+.1f}% no período; drawdown médio {bh_dd:.1f}% |",
        (
            f"| Timing aleatório (mesmo lado e duração, {t.replicates} réplicas) | bruto "
            f"{t.random_gross_pct:+.3f}%/trade (IC 95% {t.random_ci95[0]:+.3f}% a {t.random_ci95[1]:+.3f}%) "
            f"× estratégia {t.strategy_gross_pct:+.3f}% → **p = {t.p_value:.3f}**"
            + (
                f"; corrigido por {r.config.tested_hypotheses} hipóteses testadas: "
                f"**{r.timing_p_adjusted:.3f}** |"
                if r.config.tested_hypotheses > 1 else " |"
            )
        ),
        "",
        (
            f"Expectativa: {100 * o.win_rate:.1f}% × {o.avg_win_pct:+.3f}% − "
            f"{100 * (1 - o.win_rate):.1f}% × {o.avg_loss_pct:.3f}% = {o.avg_net_pct:+.3f}% por trade "
            f"| Sortino por trade: {o.sortino:.3f}"
        ),
        "",
    ]


def _row(label: str, s: TradeStats) -> str:
    pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    return (
        f"| {label} | {s.trades} | {100 * s.win_rate:.1f}% | {s.avg_gross_pct:+.3f}% "
        f"| {s.avg_cost_pct:.3f}% | {s.avg_funding_pct:+.3f}% | {s.avg_net_pct:+.3f}% | {pf} |"
    )


_HEADER = (
    "| | Trades | Win rate | Bruto/trade | Custo/trade | Funding/trade | Líquido/trade "
    "| Profit factor |\n|---|---|---|---|---|---|---|---|"
)


def to_markdown(r: LabReport) -> str:
    c = r.config
    lines = [
        f"# Laboratório: {c.strategy} — {'APROVADA' if r.verdict.approved else 'REPROVADA'}",
        "",
        f"- Período: {r.period_start:%Y-%m-%d} a {r.period_end:%Y-%m-%d} ({c.days:g} dias)",
        f"- Símbolos: {', '.join(c.symbols)} | Timeframes: {', '.join(c.intervals)}",
        (
            f"- Custos: entrada {c.entry_fee_bps} bps, saída {c.exit_fee_bps} bps, "
            f"derrapagem {c.slippage_bps} bps | saída por tempo: {c.hold_bars or 'não'} barras"
            f" | stop móvel: {'sim' if c.trailing_stop else 'não'}"
            f" | funding: {'real' if c.funding else 'não'} | parâmetros: {c.params or 'padrão'}"
            f" | sem entrada em volatilidade: {', '.join(c.skip_vol) or 'nenhum filtro'}"
        ),
        "",
        "## Veredito",
        "",
        "| Critério | Valor | Exigido | |",
        "|---|---|---|---|",
        *[f"| {ch.name} | {ch.value} | {ch.required} | {'✅' if ch.passed else '❌'} |" for ch in r.verdict.checks],
        "",
        "## Geral",
        "",
        _HEADER,
        _row("Todos", r.overall),
        "",
        (
            f"IC 95% do líquido por trade: **{r.ci95_net_pct[0]:+.4f}% a "
            f"{r.ci95_net_pct[1]:+.4f}%** | drawdown máx.: {r.max_drawdown_pct:.1f}%"
        ),
        "",
        *_benchmarks(r),
        "## Por símbolo e timeframe",
        "",
        _HEADER,
        *[_row(f"{x.symbol} {x.interval}{' (ruína)' if x.ruined else ''}", x.stats) for x in r.runs],
        "",
        "| | Retorno total | Comprar e segurar | DD máx. | DD do ativo | Maior DD (dias) | Exposição |",
        "|---|---|---|---|---|---|---|",
        *[
            f"| {x.symbol} {x.interval} | {x.total_return_pct:+.1f}% "
            f"| {x.buy_hold.return_pct if x.buy_hold else 0:+.1f}% | {x.max_drawdown_pct:.1f}% "
            f"| {x.buy_hold.max_drawdown_pct if x.buy_hold else 0:.1f}% "
            f"| {x.max_dd_duration_days:.0f} | {x.exposure_pct:.0f}% |"
            for x in r.runs
        ],
        "",
        "## Walk-forward (blocos cronológicos)",
        "",
        _HEADER,
        *[_row(f"{f.index}: {f.start:%m-%d} a {f.end:%m-%d}", f.stats) for f in r.folds],
        "",
        "## Por regime de tendência (na entrada)",
        "",
        _HEADER,
        *[_row(k, v) for k, v in r.by_trend.items()],
        "",
        "## Por regime de volatilidade (na entrada)",
        "",
        _HEADER,
        *[_row(k, v) for k, v in r.by_volatility.items()],
        "",
        "## Limitações",
        "",
        *[f"- {x}" for x in r.limitations],
        "",
    ]
    return "\n".join(lines)
