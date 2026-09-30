import json
from datetime import UTC, datetime
from decimal import Decimal

from app.backtest.report import to_console, to_dict, to_json
from app.backtest.types import BacktestResult


def _result() -> BacktestResult:
    return BacktestResult(
        symbol="BTCUSDT",
        interval="5m",
        strategy="momentum",
        initial_capital=Decimal(10000),
        final_equity=Decimal(10300),
        total_return_pct=3.0,
        trades=[],
        equity_curve=[
            (datetime(2024, 1, 1, tzinfo=UTC), Decimal(10000)),
            (datetime(2024, 1, 1, 1, tzinfo=UTC), Decimal(10300)),
        ],
        metrics={
            "num_trades": 3.0,
            "win_rate": 0.6667,
            "profit_factor": 2.0,
            "max_drawdown_pct": 1.2,
            "sharpe": 0.85,
            "avg_return_pct": 0.9,
        },
    )


def test_to_dict_is_json_friendly():
    d = to_dict(_result())
    assert d["symbol"] == "BTCUSDT"
    assert d["initial_capital"] == "10000"  # Decimal → str


def test_to_json_roundtrip():
    js = to_json(_result())
    loaded = json.loads(js)
    assert loaded["strategy"] == "momentum"
    assert loaded["metrics"]["profit_factor"] == 2.0


def test_to_console_contains_key_lines():
    txt = to_console(_result())
    assert "BTCUSDT" in txt
    assert "momentum" in txt
    assert "Total return" in txt
    assert "Win rate" in txt
    assert "Profit factor" in txt