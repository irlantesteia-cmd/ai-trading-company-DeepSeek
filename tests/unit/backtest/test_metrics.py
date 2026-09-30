from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.backtest.metrics import compute_metrics
from app.backtest.types import BacktestTrade
from app.core.enums import MarketType, OrderSide, SignalDirection


def _trade(pnl: str) -> BacktestTrade:
    return BacktestTrade(
        signal_id="s",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        side=OrderSide.BUY,
        entry_time=datetime(2024, 1, 1, tzinfo=UTC),
        entry_price=Decimal(100),
        quantity=Decimal(1),
        stop_price=Decimal(99),
        target_price=Decimal(105),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=UTC),
        exit_price=Decimal(101),
        exit_reason="target",
        pnl=Decimal(pnl),
        fees=Decimal(0),
        return_pct=1.0,
    )


def test_metrics_with_mixed_trades():
    trades = [_trade("100"), _trade("200"), _trade("-50"), _trade("-150")]
    equity = [
        (datetime(2024, 1, 1, tzinfo=UTC), Decimal(10000)),
        (datetime(2024, 1, 1, 1, tzinfo=UTC), Decimal(10100)),
        (datetime(2024, 1, 1, 2, tzinfo=UTC), Decimal(10300)),
        (datetime(2024, 1, 1, 3, tzinfo=UTC), Decimal(10250)),
        (datetime(2024, 1, 1, 4, tzinfo=UTC), Decimal(10100)),
    ]
    m = compute_metrics(
        initial=Decimal(10000),
        final=Decimal(10100),
        trades=trades,
        equity_curve=equity,
    )
    assert m["num_trades"] == 4.0
    assert m["win_rate"] == 0.5
    assert m["gross_profit"] == 300.0
    assert m["gross_loss"] == -200.0
    assert m["profit_factor"] == pytest.approx(1.5)
    assert m["payoff_ratio"] == pytest.approx(1.5)  # 150 / 100
    assert m["max_drawdown_pct"] > 0
    assert m["max_drawdown_pct"] < 5
    assert "sharpe" in m


def test_metrics_with_no_trades():
    m = compute_metrics(
        initial=Decimal(10000),
        final=Decimal(10000),
        trades=[],
        equity_curve=[(datetime(2024, 1, 1, tzinfo=UTC), Decimal(10000))],
    )
    assert m["num_trades"] == 0.0
    assert m["total_return_pct"] == 0.0
    assert "win_rate" not in m


def test_metrics_with_only_wins_profit_factor_inf():
    trades = [_trade("100"), _trade("200")]
    m = compute_metrics(
        initial=Decimal(10000),
        final=Decimal(10300),
        trades=trades,
        equity_curve=[
            (datetime(2024, 1, 1, tzinfo=UTC), Decimal(10000)),
            (datetime(2024, 1, 1, 1, tzinfo=UTC), Decimal(10100)),
            (datetime(2024, 1, 1, 2, tzinfo=UTC), Decimal(10300)),
        ],
    )
    assert m["profit_factor"] == float("inf")
    assert m["win_rate"] == 1.0