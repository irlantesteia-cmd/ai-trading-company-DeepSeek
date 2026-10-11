"""L4a: backtest de carteira — momentum entre ativos (comprado e vendido)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.gate import GateCheck, evaluate_gate
from app.backtest.portfolio import (
    PortfolioCosts,
    XSMomentumParams,
    run_xs_momentum,
    select_book,
)
from app.backtest.portfolio_lab import PortfolioLabConfig, run_portfolio_lab, to_markdown
from app.backtest.stats import TradeStats
from app.core.enums import MarketType
from app.domain.models.market import Candle
from scripts.strategy_lab import parse_args

T0 = datetime(2024, 1, 1, tzinfo=UTC)  # segunda-feira
NO_COSTS = PortfolioCosts(fee_bps=0.0, slippage_bps=0.0)


def daily(symbol: str, closes: list[float], *, start: datetime = T0) -> list[Candle]:
    """Candles diários: abertura = fechamento anterior (como na Binance)."""
    out: list[Candle] = []
    for i, c in enumerate(closes):
        close = Decimal(str(c))
        open_ = Decimal(str(closes[i - 1])) if i else close
        t = start + timedelta(days=i)
        out.append(Candle(
            symbol=symbol, market_type=MarketType.FUTURES, interval="1d",
            open_time=t, close_time=t + timedelta(days=1, milliseconds=-1),
            open=open_, high=max(open_, close), low=min(open_, close), close=close,
            volume=Decimal(1), trades=1,
        ))
    return out


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# A sobe, B parado, C cai; decisão no fechamento do dia 2, execução no dia 3.
BASE = {
    "A": [100.0, 100.0, 110.0, 121.0],
    "B": [100.0, 100.0, 100.0, 100.0],
    "C": [100.0, 100.0, 90.0, 81.0],
}
DAILY_1 = XSMomentumParams(lookback_days=2, top_k=1, rebalance_days=1)


def _run(closes=BASE, params=DAILY_1, costs=NO_COSTS, **kw):
    return run_xs_momentum({s: daily(s, c) for s, c in closes.items()}, params=params, costs=costs, **kw)


# ------------------------------------------------------------------ seleção
def test_select_book_ranks_and_breaks_ties_by_name():
    scores = {"A": 0.3, "B": 0.1, "C": -0.2, "D": 0.0}
    assert select_book(scores, 1) == {"A": 1, "C": -1}
    assert select_book(scores, 2) == {"A": 1, "B": 1, "C": -1, "D": -1}
    assert select_book(scores, 3) == {}  # faltam símbolos para 3 + 3
    assert select_book({"X": 0.0, "Y": 0.0}, 1) == {"Y": 1, "X": -1}


def test_params_reject_unknown_and_non_positive():
    with pytest.raises(ValueError):
        XSMomentumParams(top_k=0)
    with pytest.raises(ValueError):
        XSMomentumParams(stop_atr_mult=2)


# ------------------------------------------------------------- simulação
def test_long_winner_short_loser_hand_computed():
    r = _run()
    (period,) = r.periods
    assert period.start == T0 + timedelta(days=3)  # abertura do dia seguinte à decisão
    # 3000 de equity → 1500 por lado; A +10% e C -10% → +150 em cada lado
    assert period.long_pct == pytest.approx(5.0)
    assert period.short_pct == pytest.approx(5.0)
    assert period.net_pct == pytest.approx(10.0)
    assert {(x.symbol, x.side) for x in r.legs} == {("A", "LONG"), ("C", "SHORT")}
    assert all(x.return_pct == pytest.approx(10.0) for x in r.legs)
    assert r.equity_curve[-1][1] == pytest.approx(3300.0)


def test_decision_ignores_data_after_the_decision_close():
    changed = {**BASE, "C": [100.0, 100.0, 90.0, 200.0]}  # muda só o dia da execução
    first = {(x.symbol, x.side) for x in _run().legs}
    second = _run(changed)
    assert {(x.symbol, x.side) for x in second.legs} == first
    assert second.periods[0].short_pct < 0  # a venda de C perdeu no dia 3


def test_costs_charged_only_on_traded_notional():
    closes = {
        "A": [100 * 1.01 ** k for k in range(8)],
        "B": [100.0] * 8,
        "C": [100 * 0.99 ** k for k in range(8)],
    }
    r = _run(closes, XSMomentumParams(lookback_days=1, top_k=1, rebalance_days=1),
             PortfolioCosts(fee_bps=10.0, slippage_bps=0.0))
    first, *rest = r.periods
    assert first.turnover == pytest.approx(1.0)        # abre 1500 + 1500
    assert first.cost_pct == pytest.approx(0.1)        # 3000 × 10 bps / 3000
    middle = rest[:-1]
    assert middle and all(p.turnover < 0.05 for p in middle)  # mesma carteira: só reajuste
    assert rest[-1].cost_pct > max(p.cost_pct for p in middle)  # liquidação no fim


def test_funding_long_pays_short_receives():
    t = _ms(T0 + timedelta(days=3, hours=8))
    both = _run(funding={"A": [(t, 0.001)], "C": [(t, 0.001)]}).periods[0]
    assert both.funding_pct == pytest.approx(0.0)  # comprado paga 1,5, vendido recebe 1,5
    only_long = _run(funding={"A": [(t, 0.001)]}).periods[0]
    assert only_long.funding_pct == pytest.approx(0.05)  # 1,5 / 3000
    only_short = _run(funding={"C": [(t, 0.001)]}).periods[0]
    assert only_short.funding_pct == pytest.approx(-0.05)


def test_funding_window_excludes_the_open_and_includes_next_open():
    day3 = T0 + timedelta(days=3)
    at_open = _run(funding={"A": [(_ms(day3), 0.01)]}).periods[0]
    at_next = _run(funding={"A": [(_ms(day3 + timedelta(days=1)), 0.01)]}).periods[0]
    assert at_open.funding_pct == 0.0
    assert at_next.funding_pct == pytest.approx(0.5)


def test_symbol_listed_later_waits_for_full_lookback():
    closes = {
        "A": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
        "B": [100.0] * 7,
        "C": [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0],
    }
    candles = {s: daily(s, c) for s, c in closes.items()}
    candles["D"] = daily("D", [10.0, 20.0, 40.0, 80.0, 160.0], start=T0 + timedelta(days=2))
    r = run_xs_momentum(candles, params=DAILY_1, costs=NO_COSTS)
    longs = [(x.start, x.symbol) for x in r.legs if x.side == "LONG"]
    # D tem candle a partir do dia 2: primeira decisão possível no fechamento do dia 4
    assert all(s != "D" for start, s in longs if start <= T0 + timedelta(days=4))
    assert ("D" in {s for start, s in longs if start == T0 + timedelta(days=5)})


def test_weekly_rebalance_on_mondays_and_balanced_book():
    rng = random.Random(7)
    closes = {}
    for s in "ABCDEFGH":
        px, series = 100.0, []
        for _ in range(90):
            px *= 1 + rng.gauss(0, 0.03)
            series.append(px)
        closes[s] = series
    r = _run(closes, XSMomentumParams(lookback_days=14, top_k=3, rebalance_days=7))
    assert len(r.periods) >= 10
    assert all(p.start.weekday() == 0 for p in r.periods)
    for p in r.periods:
        legs = [x for x in r.legs if x.start == p.start]
        assert p.positions == 6
        assert sum(x.side == "LONG" for x in legs) == sum(x.side == "SHORT" for x in legs) == 3


def test_book_stays_flat_without_enough_symbols():
    r = _run({"A": BASE["A"], "C": BASE["C"]}, XSMomentumParams(lookback_days=2, top_k=2, rebalance_days=1))
    assert r.periods == [] and r.legs == []
    assert r.equity_curve[-1][1] == pytest.approx(3000.0)


# --------------------------------------------------------------------- gate
def test_gate_sample_size_and_extra_checks():
    overall = TradeStats(trades=10, avg_net_pct=1.0, profit_factor=2.0)
    v = evaluate_gate(overall=overall, ci=(0.1, 2.0), folds=[], max_drawdown_pct=5.0,
                      sample_size=400, extra_checks=[GateCheck(name="periodos", passed=False,
                                                               value="10", required=">= 100")])
    checks = {c.name: c for c in v.checks}
    assert checks["trades"].passed and checks["trades"].value == "400"
    assert not checks["periodos"].passed and not v.approved


# ---------------------------------------------------------------------- lab
async def test_portfolio_lab_report_and_markdown():
    rng = random.Random(3)
    data = {}
    for s in [f"S{k}" for k in range(8)]:
        px, series = 100.0, []
        for _ in range(200):
            px *= 1 + rng.gauss(0.0005, 0.03)
            series.append(px)
        data[s] = daily(s, series)

    async def loader(symbol, interval, days):
        assert interval == "1d"
        return data[symbol]

    async def funding_loader(symbol, days):
        return [(_ms(c.open_time + timedelta(hours=8)), 0.0001) for c in data[symbol]]

    report = await run_portfolio_lab(
        PortfolioLabConfig(symbols=list(data), days=200, funding=True,
                           params=XSMomentumParams(lookback_days=28, top_k=2)),
        loader=loader, funding_loader=funding_loader,
    )
    checks = {c.name: c for c in report.verdict.checks}
    assert set(checks) == {"trades", "ic95_limite_inferior", "blocos_positivos",
                           "profit_factor", "drawdown_max", "periodos"}
    assert checks["trades"].value == str(report.positions) == str(4 * report.overall.trades)
    assert not checks["periodos"].passed  # ~24 semanas < 100
    assert sum(x.longs + x.shorts for x in report.by_symbol) == report.positions
    md = to_markdown(report)
    assert "Lado comprado" in md and "funding: real" in md and "REPROVADA" in md


async def test_portfolio_lab_needs_enough_history():
    async def loader(symbol, interval, days):
        return daily(symbol, [100.0] * 5)

    with pytest.raises(ValueError):
        await run_portfolio_lab(PortfolioLabConfig(symbols=["A", "B", "C", "D", "E", "F"]), loader=loader)


def test_cli_builds_portfolio_config():
    cfg = parse_args(["--strategy", "xs_momentum", "--param", "top_k=2", "--fee", "maker", "--funding"])
    assert isinstance(cfg, PortfolioLabConfig)
    assert cfg.params.top_k == 2 and cfg.params.lookback_days == 28
    assert cfg.fee_bps == 5.0 and cfg.funding
    with pytest.raises(SystemExit):
        parse_args(["--strategy", "xs_momentum", "--param", "channel_period=20"])
