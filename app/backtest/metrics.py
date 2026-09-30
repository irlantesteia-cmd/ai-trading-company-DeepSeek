from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import mean, pstdev

from app.backtest.types import BacktestTrade


def compute_metrics(
    *,
    initial: Decimal,
    final: Decimal,
    trades: list[BacktestTrade],
    equity_curve: list[tuple[datetime, Decimal]],
) -> dict[str, float]:
    metrics: dict[str, float] = {}

    metrics["num_trades"] = float(len(trades))
    metrics["total_return_pct"] = (
        float((final - initial) / initial * 100) if initial > 0 else 0.0
    )

    if trades:
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]

        metrics["win_rate"] = len(wins) / len(trades)
        gross_win = sum((t.pnl for t in wins), Decimal(0))
        gross_loss = sum((t.pnl for t in losses), Decimal(0))
        metrics["gross_profit"] = float(gross_win)
        metrics["gross_loss"] = float(gross_loss)

        if gross_loss < 0:
            metrics["profit_factor"] = float(gross_win / abs(gross_loss))
        else:
            metrics["profit_factor"] = float("inf") if gross_win > 0 else 0.0

        avg_win = gross_win / len(wins) if wins else Decimal(0)
        avg_loss = abs(gross_loss) / len(losses) if losses else Decimal(0)
        metrics["avg_win"] = float(avg_win)
        metrics["avg_loss"] = float(avg_loss)

        if avg_loss > 0:
            metrics["payoff_ratio"] = float(avg_win / avg_loss)
        else:
            metrics["payoff_ratio"] = float("inf") if avg_win > 0 else 0.0

        metrics["avg_return_pct"] = mean([t.return_pct for t in trades])

    if len(equity_curve) >= 2:
        values = [float(v) for _, v in equity_curve]
        returns = [
            (values[i] - values[i - 1]) / values[i - 1]
            for i in range(1, len(values))
            if values[i - 1] > 0
        ]
        if len(returns) >= 2:
            mu = mean(returns)
            sigma = pstdev(returns)
            metrics["sharpe"] = (
                (mu / sigma) * (len(returns) ** 0.5) if sigma > 0 else 0.0
            )
            metrics["volatility"] = sigma * (len(returns) ** 0.5)

        peak = values[0]
        max_dd = 0.0
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        metrics["max_drawdown_pct"] = max_dd * 100

    return metrics