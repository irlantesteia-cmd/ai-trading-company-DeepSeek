"""Fase 4a: benchmarks (comprar e segurar, timing aleatório) e métricas novas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.benchmarks import buy_and_hold, random_timing_test
from app.backtest.metrics import max_drawdown_duration_days
from app.backtest.stats import summarize
from app.backtest.types import BacktestTrade
from app.core.enums import MarketType, OrderSide, SignalDirection
from tests.unit.strategies.conftest import make_candles

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _trade(entry_bar: int, held: int, gross: float, net: float,
           direction: SignalDirection = SignalDirection.LONG) -> BacktestTrade:
    return BacktestTrade(
        symbol="AAA", market_type=MarketType.FUTURES, direction=direction,
        side=OrderSide.BUY if direction is SignalDirection.LONG else OrderSide.SELL,
        entry_time=T0 + timedelta(minutes=5 * entry_bar), exit_time=T0, entry_price=Decimal(100),
        exit_price=Decimal(100), quantity=Decimal(1), stop_price=Decimal(90), target_price=None,
        exit_reason="time", pnl=Decimal(str(net)), fees=Decimal(0), return_pct=net,
        gross_return_pct=gross, bars_held=held, signal_id="s",
    )


def test_buy_and_hold_return_and_drawdown():
    bh = buy_and_hold(make_candles([100.0, 120.0, 90.0, 110.0]))
    assert bh.return_pct == pytest.approx(10.0)       # abre a 100 (open = 1º close), fecha 110
    assert bh.max_drawdown_pct == pytest.approx(25.0)  # 120 -> 90


def test_random_timing_flags_real_skill_and_not_luck():
    # Série com ciclos: sobe 20 barras, cai 20. Quem compra no fundo e
    # segura 20 barras tem timing real; quem compra em barras quaisquer, não.
    closes = []
    for cycle in range(40):
        closes += [100 + k for k in range(20)] + [120 - k for k in range(20)]
    candles = make_candles([float(c) for c in closes])
    bottoms = [40 * c + 1 for c in range(39)]
    skilled = [_trade(b, 19, 100 * (float(candles[b + 18].close) / float(candles[b].open) - 1), 0.0)
               for b in bottoms]
    result = random_timing_test([(candles, skilled)], replicates=300, seed=1)
    assert result.trades == len(skilled)
    assert result.strategy_gross_pct > result.random_ci95[1]
    assert result.p_value < 0.01

    lucky = [_trade(b + 10, 19, result.random_gross_pct, 0.0) for b in bottoms]
    same = random_timing_test([(candles, lucky)], replicates=300, seed=1)
    assert same.p_value > 0.2  # igual à média do acaso: nada a comemorar


def test_random_timing_respects_side():
    candles = make_candles([100.0 + k for k in range(200)])  # só sobe
    shorts = [_trade(10 * k, 5, 0.0, 0.0, SignalDirection.SHORT) for k in range(15)]
    result = random_timing_test([(candles, shorts)], replicates=100)
    assert result.random_gross_pct < 0  # vender numa série que só sobe perde no acaso também
    assert result.p_value < 0.05        # bruto 0 > o acaso vendido


def test_timing_test_without_trades_is_neutral():
    result = random_timing_test([(make_candles([1.0, 2.0]), [])])
    assert result.trades == 0 and result.p_value == 1.0


def test_trade_stats_expectancy_and_sortino():
    s = summarize([_trade(0, 1, 0, 2.0), _trade(0, 1, 0, -1.0), _trade(0, 1, 0, 1.0), _trade(0, 1, 0, -2.0)])
    assert s.avg_win_pct == pytest.approx(1.5) and s.avg_loss_pct == pytest.approx(1.5)
    expectancy = s.win_rate * s.avg_win_pct - (1 - s.win_rate) * s.avg_loss_pct
    assert expectancy == pytest.approx(s.avg_net_pct) == pytest.approx(0.0)
    assert s.sortino == pytest.approx(0.0)
    assert summarize([_trade(0, 1, 0, 1.0), _trade(0, 1, 0, -1.0), _trade(0, 1, 0, 3.0)]).sortino > 0


def test_drawdown_duration_from_peak_to_recovery():
    curve = [(T0 + timedelta(days=d), Decimal(v)) for d, v in
             [(0, 100), (1, 110), (3, 90), (8, 105), (11, 111), (12, 115), (13, 100)]]
    # 110 (dia 1) só é superado no dia 11 → 10 dias; o último trecho (dia 12 → 13) dura 1
    assert max_drawdown_duration_days(curve) == pytest.approx(10.0)
    assert max_drawdown_duration_days([(T0, Decimal(1)), (T0 + timedelta(days=2), Decimal(2))]) == 0.0
