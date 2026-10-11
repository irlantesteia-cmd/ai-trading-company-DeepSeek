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

from app.backtest.data import load_candles
from app.backtest.engine import BacktestEngine
from app.backtest.gate import GateCriteria, GateVerdict, evaluate_gate
from app.backtest.regime import classify_regimes
from app.backtest.stats import FoldStats, TradeStats, bootstrap_mean_ci, summarize, walk_forward
from app.backtest.types import BacktestConfig, BacktestTrade
from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.strategies import MeanReversionStrategy, MomentumStrategy
from app.strategies.base import Strategy

STRATEGIES: dict[str, type[Strategy]] = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
}

LIMITATIONS = [
    (
        "Cada símbolo é simulado isolado: limites de portfólio do bot ao vivo "
        "(máx. posições, grupos correlacionados, trava de trading) não entram."
    ),
    "Stop e alvo na mesma barra contam como stop (pessimista).",
    "Médias por trade em % do notional; o drawdown é o pior entre as execuções.",
]

CandleLoader = Callable[[str, str, float], Awaitable[list[Candle]]]


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
    max_position_notional: float | None = 2000.0
    initial_capital: float = 3000.0
    risk_per_trade_pct: float = 0.01
    folds: int = 5
    seed: int = 42
    criteria: GateCriteria = Field(default_factory=GateCriteria)


class RunSummary(BaseModel):
    symbol: str
    interval: str
    candles: int
    max_drawdown_pct: float
    ruined: bool = False  # capital simulado zerou: entradas pararam ali
    stats: TradeStats


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


async def run_lab(config: LabConfig, *, loader: CandleLoader | None = None) -> LabReport:
    load = loader or _default_loader
    engine = BacktestEngine(
        BacktestConfig(
            initial_capital=Decimal(str(config.initial_capital)),
            risk_per_trade_pct=config.risk_per_trade_pct,
            entry_fee_bps=config.entry_fee_bps,
            exit_fee_bps=config.exit_fee_bps,
            slippage_bps=config.slippage_bps,
            max_holding_bars=config.hold_bars,
            max_position_notional=(
                Decimal(str(config.max_position_notional))
                if config.max_position_notional is not None
                else None
            ),
        )
    )
    runs: list[RunSummary] = []
    trades: list[BacktestTrade] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    for interval in config.intervals:
        for symbol in config.symbols:
            candles = await load(symbol, interval, config.days)
            if not candles:
                continue
            strategy = build_strategy(config.strategy, config.params)
            result = engine.run(
                symbol=symbol,
                interval=interval,
                market_type=MarketType.FUTURES,
                candles=candles,
                strategy=strategy,
                regimes=classify_regimes(candles),
            )
            trades.extend(result.trades)
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
                )
            )
    if not runs:
        raise ValueError("nenhum candle carregado")

    period_start, period_end = min(starts), max(ends)
    overall = summarize(trades)
    ci = bootstrap_mean_ci([t.return_pct for t in trades], seed=config.seed)
    folds = walk_forward(trades, start=period_start, end=period_end, folds=config.folds)
    max_dd = max(r.max_drawdown_pct for r in runs)
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
            overall=overall, ci=ci, folds=folds, max_drawdown_pct=max_dd, criteria=config.criteria
        ),
    )


def _group(trades: list[BacktestTrade], key) -> dict[str, TradeStats]:
    buckets: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        buckets.setdefault(key(t) or "UNKNOWN", []).append(t)
    return {k: summarize(v) for k, v in sorted(buckets.items())}


# ------------------------------------------------------------------ relatório
def _row(label: str, s: TradeStats) -> str:
    pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    return (
        f"| {label} | {s.trades} | {100 * s.win_rate:.1f}% | {s.avg_gross_pct:+.3f}% "
        f"| {s.avg_cost_pct:.3f}% | {s.avg_net_pct:+.3f}% | {pf} |"
    )


_HEADER = "| | Trades | Win rate | Bruto/trade | Custo/trade | Líquido/trade | Profit factor |\n|---|---|---|---|---|---|---|"


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
            f" | parâmetros: {c.params or 'padrão'}"
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
        "## Por símbolo e timeframe",
        "",
        _HEADER,
        *[_row(f"{x.symbol} {x.interval}{' (ruína)' if x.ruined else ''}", x.stats) for x in r.runs],
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
