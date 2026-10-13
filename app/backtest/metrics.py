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
        # Expectativa por trade em % do notional: bruta (só preço), custo
        # (taxas + derrapagem) e líquida. Com bruta ~0, o líquido é -custo.
        metrics["avg_gross_return_pct"] = mean([t.gross_return_pct for t in trades])
        metrics["avg_funding_pct"] = mean([t.funding_pct for t in trades])
        # Custo = corretagem + derrapagem; funding fica separado.
        metrics["avg_cost_pct"] = (
            metrics["avg_gross_return_pct"] - metrics["avg_return_pct"] - metrics["avg_funding_pct"]
        )
        metrics["avg_bars_held"] = mean([t.bars_held for t in trades])
        metrics["total_fees"] = float(sum((t.fees for t in trades), Decimal(0)))
        for reason in ("stop", "target", "time", "end"):
            metrics[f"exits_{reason}"] = float(sum(1 for t in trades if t.exit_reason == reason))

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

        metrics["max_dd_duration_days"] = max_drawdown_duration_days(equity_curve)

    return metrics


def max_drawdown_duration_days(equity_curve: list[tuple[datetime, Decimal]]) -> float:
    """Maior tempo (dias) abaixo de um pico anterior, do pico até recuperá-lo
    (ou até o fim da curva, se não recuperou)."""
    if len(equity_curve) < 2:
        return 0.0
    peak_value, peak_time = equity_curve[0][1], equity_curve[0][0]
    longest = 0.0
    underwater = False
    for ts, value in equity_curve[1:]:
        if value >= peak_value:
            if underwater:
                longest = max(longest, (ts - peak_time).total_seconds() / 86400)
            peak_value, peak_time, underwater = value, ts, False
        else:
            underwater = True
    last_ts, last_value = equity_curve[-1]
    if last_value < peak_value:
        longest = max(longest, (last_ts - peak_time).total_seconds() / 86400)
    return longest