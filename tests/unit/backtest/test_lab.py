"""L2 do laboratório: estatística, walk-forward, regime, gate e comando."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.gate import GateCriteria, evaluate_gate
from app.backtest.lab import LabConfig, build_strategy, run_lab, to_markdown
from app.backtest.regime import RegimeParams, classify_regimes
from app.backtest.stats import FoldStats, TradeStats, bootstrap_mean_ci, summarize, walk_forward
from app.backtest.types import BacktestTrade
from app.core.enums import MarketType, OrderSide, SignalDirection
from app.strategies import MomentumStrategy
from scripts.strategy_lab import parse_args
from tests.unit.strategies.conftest import make_candles

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(net: float, *, gross: float | None = None, at: datetime = T0) -> BacktestTrade:
    return BacktestTrade(
        signal_id="s", symbol="BTCUSDT", market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG, side=OrderSide.BUY, entry_time=at,
        entry_price=Decimal(100), quantity=Decimal(1), stop_price=Decimal(99),
        return_pct=net, gross_return_pct=net + 0.14 if gross is None else gross,
    )


# ------------------------------------------------------------- stats
def test_summarize_net_gross_cost_and_profit_factor():
    s = summarize([_trade(1.0), _trade(-0.5), _trade(-0.5)])
    assert s.trades == 3
    assert s.win_rate == pytest.approx(1 / 3)
    assert s.avg_net_pct == pytest.approx(0.0)
    assert s.avg_cost_pct == pytest.approx(0.14)
    assert s.profit_factor == pytest.approx(1.0)


def test_bootstrap_is_reproducible_and_contains_the_mean():
    rng = random.Random(3)
    values = [rng.gauss(0.05, 0.5) for _ in range(800)]
    a = bootstrap_mean_ci(values, seed=1)
    b = bootstrap_mean_ci(values, seed=1)
    mean = sum(values) / len(values)
    assert a == b
    assert a[0] < mean < a[1]
    assert a[1] - a[0] < 0.1  # 800 trades: intervalo estreito


def test_walk_forward_splits_chronologically_without_overlap():
    trades = [_trade(0.1, at=T0 + timedelta(days=d)) for d in range(10)]
    folds = walk_forward(trades, start=T0, end=T0 + timedelta(days=10), folds=5)
    assert [f.stats.trades for f in folds] == [2, 2, 2, 2, 2]
    assert all(folds[i].end == folds[i + 1].start for i in range(4))


# ------------------------------------------------------------ regime
def _trend_closes(n: int, drift: float, seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= 1 + drift + rng.gauss(0, 0.001)
        out.append(price)
    return out


def test_regime_detects_up_and_down_trends():
    p = RegimeParams(ema_period=50, slope_bars=10, vol_lookback=100)
    up = classify_regimes(make_candles(_trend_closes(400, 0.003)), p)
    down = classify_regimes(make_candles(_trend_closes(400, -0.003)), p)
    assert up[-1][0] == "UP"
    assert down[-1][0] == "DOWN"
    assert up[0] == ("UNKNOWN", "UNKNOWN")


def test_regime_detects_volatility_spike():
    rng = random.Random(9)
    closes, price = [], 100.0
    for i in range(600):
        price *= 1 + rng.gauss(0, 0.02 if i >= 580 else 0.002)
        closes.append(price)
    labels = classify_regimes(make_candles(closes), RegimeParams(vol_lookback=200))
    assert labels[-1][1] == "HIGH"


def test_regime_labels_never_look_ahead():
    """Cortar o futuro não pode mudar nenhum rótulo do passado."""
    candles = make_candles(_trend_closes(700, 0.0005, seed=11))
    full = classify_regimes(candles)
    cut = classify_regimes(candles[:450])
    assert cut == full[:450]


# -------------------------------------------------------------- gate
def _folds(signs: list[float]) -> list[FoldStats]:
    return [
        FoldStats(index=i, start=T0, end=T0, stats=TradeStats(trades=10, avg_net_pct=s))
        for i, s in enumerate(signs, 1)
    ]


def test_gate_approves_only_when_every_criterion_passes():
    good = {
        "overall": TradeStats(trades=400, profit_factor=1.3),
        "ci": (0.01, 0.2),
        "folds": _folds([0.1, 0.1, 0.1, 0.1, -0.1]),
        "max_drawdown_pct": 12.0,
    }
    assert evaluate_gate(**good).approved is True

    failing = {
        "overall": {"overall": TradeStats(trades=299, profit_factor=1.3)},
        "ci": {"ci": (-0.01, 0.2)},
        "folds": {"folds": _folds([0.1, 0.1, 0.1, -0.1, -0.1])},
        "pf": {"overall": TradeStats(trades=400, profit_factor=1.1)},
        "dd": {"max_drawdown_pct": 20.1},
    }
    for name, change in failing.items():
        verdict = evaluate_gate(**{**good, **change})
        assert verdict.approved is False, name
        assert sum(not c.passed for c in verdict.checks) == 1, name


def test_gate_thresholds_are_configurable():
    verdict = evaluate_gate(
        overall=TradeStats(trades=50, profit_factor=2), ci=(0.1, 0.2),
        folds=_folds([1, 1, 1, 1, 1]), max_drawdown_pct=5,
        criteria=GateCriteria(min_trades=30),
    )
    assert verdict.approved is True


# ------------------------------------------------- estratégia e lab
def test_build_strategy_validates_name_and_params():
    s = build_strategy("momentum", {"stop_atr_mult": 2.0, "slow_period": 30.0})
    assert isinstance(s, MomentumStrategy) and s.slow_period == 30 and s.stop_atr_mult == 2.0
    with pytest.raises(ValueError, match="desconhecida"):
        build_strategy("magic", {})
    with pytest.raises(ValueError, match="inválidos"):
        build_strategy("momentum", {"nope": 1.0})


async def test_run_lab_end_to_end_with_fake_data():
    series = {s: make_candles(_trend_closes(1500, 0.0004, seed=k)) for k, s in enumerate(["AAA", "BBB"])}

    async def loader(symbol, interval, days):
        return series[symbol]

    report = await run_lab(
        LabConfig(symbols=["AAA", "BBB"], intervals=["5m"], hold_bars=6, max_position_notional=None),
        loader=loader,
    )
    assert [r.symbol for r in report.runs] == ["AAA", "BBB"]
    assert report.overall.trades == sum(r.stats.trades for r in report.runs) > 0
    assert sum(f.stats.trades for f in report.folds) == report.overall.trades
    assert sum(s.trades for s in report.by_trend.values()) == report.overall.trades
    assert len(report.verdict.checks) == 6  # 5 do gate + timing contra o acaso (Fase 4)
    assert report.verdict.checks[-1].name == "timing_vs_aleatorio"
    md = to_markdown(report)
    assert "## Veredito" in md and ("APROVADA" in md or "REPROVADA" in md)


def test_cli_parses_fees_params_and_lists():
    cfg = parse_args(["--fee", "maker", "--param", "stop_atr_mult=2", "--intervals", "5m,1h",
                      "--symbols", "btcusdt", "--hold-bars", "6"])
    assert (cfg.entry_fee_bps, cfg.exit_fee_bps) == (2.0, 5.0)
    assert cfg.params == {"stop_atr_mult": 2.0}
    assert cfg.intervals == ["5m", "1h"] and cfg.symbols == ["BTCUSDT"]
    assert parse_args(["--fee", "3"]).entry_fee_bps == 3.0
    with pytest.raises(SystemExit):
        parse_args(["--param", "semigual"])
