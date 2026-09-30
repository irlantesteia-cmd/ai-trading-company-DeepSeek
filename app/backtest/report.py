from __future__ import annotations

import json
import math

from app.backtest.types import BacktestResult


def to_dict(result: BacktestResult) -> dict:
    return result.model_dump(mode="json")


def to_json(result: BacktestResult, *, indent: int = 2) -> str:
    return json.dumps(to_dict(result), indent=indent, default=str)


def _fmt(v: float) -> str:
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return f"{v:.3f}"


def to_console(result: BacktestResult) -> str:
    m = result.metrics
    lines = [
        f"=== Backtest: {result.symbol} ({result.interval}) — {result.strategy} ===",
        f"Initial capital : {result.initial_capital}",
        f"Final equity    : {result.final_equity}",
        f"Total return    : {result.total_return_pct:.2f}%",
        f"Trades          : {int(m.get('num_trades', 0))}",
    ]
    if "win_rate" in m:
        lines.append(f"Win rate        : {m['win_rate'] * 100:.2f}%")
    if "profit_factor" in m:
        lines.append(f"Profit factor   : {_fmt(m['profit_factor'])}")
    if "payoff_ratio" in m:
        lines.append(f"Payoff ratio    : {_fmt(m['payoff_ratio'])}")
    if "max_drawdown_pct" in m:
        lines.append(f"Max drawdown    : {m['max_drawdown_pct']:.2f}%")
    if "sharpe" in m:
        lines.append(f"Sharpe (bar)    : {m['sharpe']:.3f}")
    if "avg_return_pct" in m:
        lines.append(f"Avg trade ret   : {m['avg_return_pct']:.3f}%")
    return "\n".join(lines)