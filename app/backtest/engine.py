from __future__ import annotations

import logging
from decimal import Decimal

from app.backtest.metrics import compute_metrics
from app.backtest.types import BacktestConfig, BacktestResult, BacktestTrade
from app.core.enums import MarketType, OrderSide, SignalDirection
from app.domain.models.market import Candle
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.strategies.base import Strategy

logger = logging.getLogger(__name__)

_QTY_STEP = Decimal("0.00000001")


class BacktestEngine:
    """Backtest determinístico barra-a-barra.

    Convenções:
      - Sinal gerado no fechamento da barra i → ordem executada na abertura da barra i+1.
      - Stop/target checados a partir da barra i+1 (usando low/high).
      - Se stop e target são atingíveis na mesma barra, o stop vence (pessimista).
      - Slippage aplicado contra a posição: BUY paga a mais, SELL recebe a menos.
      - Fees cobradas sobre notional de entrada E saída.
      - Ao final do histórico, posição aberta é fechada ao preço da última barra.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()
        self._fee_ratio = Decimal(str(self._config.fee_bps)) / Decimal(10000)
        self._slip_ratio = Decimal(str(self._config.slippage_bps)) / Decimal(10000)

    @property
    def config(self) -> BacktestConfig:
        return self._config

    def run(
        self,
        *,
        symbol: str,
        interval: str,
        market_type: MarketType,
        candles: list[Candle],
        strategy: Strategy,
    ) -> BacktestResult:
        if not candles:
            raise ValueError("candles vazio")

        cfg = self._config
        equity = cfg.initial_capital
        equity_curve: list[tuple] = [(candles[0].open_time, equity)]
        trades: list[BacktestTrade] = []
        open_trade: BacktestTrade | None = None
        pending: Signal | None = None

        for bar in candles:
            # 1) Enter pending
            if pending is not None and open_trade is None:
                open_trade = self._enter(pending, bar, equity)
                pending = None

            # 2) Exit check
            if open_trade is not None:
                reason, exit_price = self._check_exit(open_trade, bar)
                if reason is not None and exit_price is not None:
                    closed, net_pnl, _fees = self._close(
                        open_trade, bar.close_time, exit_price, reason
                    )
                    trades.append(closed)
                    equity += net_pnl
                    equity_curve.append((bar.close_time, equity))
                    open_trade = None

            # 3) New signal (apenas se flat)
            if open_trade is None:
                ctx = StrategyContext(
                    symbol=symbol,
                    market_type=market_type,
                    interval=interval,
                    candles=candles[: candles.index(bar) + 1],
                )
                sig = strategy.generate(ctx)
                if (
                    sig is not None
                    and sig.suggested_entry is not None
                    and sig.suggested_stop is not None
                ):
                    pending = sig

        # Fecha posição aberta ao final
        if open_trade is not None:
            last = candles[-1]
            closed, net_pnl, _fees = self._close(
                open_trade, last.close_time, last.close, "end"
            )
            trades.append(closed)
            equity += net_pnl
            equity_curve.append((last.close_time, equity))

        metrics = compute_metrics(
            initial=cfg.initial_capital,
            final=equity,
            trades=trades,
            equity_curve=equity_curve,
        )
        total_return_pct = float(
            (equity - cfg.initial_capital) / cfg.initial_capital * 100
        )

        return BacktestResult(
            symbol=symbol,
            interval=interval,
            strategy=strategy.name,
            initial_capital=cfg.initial_capital,
            final_equity=equity,
            total_return_pct=total_return_pct,
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
        )

    # ------------------------------------------------------------------ internals
    def _enter(self, signal: Signal, bar: Candle, equity: Decimal) -> BacktestTrade:
        side = (
            OrderSide.BUY
            if signal.direction == SignalDirection.LONG
            else OrderSide.SELL
        )
        entry_price = self._apply_slippage(bar.open, side, entry=True)

        stop = signal.suggested_stop
        assert stop is not None  # filtrado em run()
        stop_distance = abs(entry_price - stop)
        if stop_distance <= 0:
            raise ValueError(f"stop_distance inválido: {stop_distance}")

        risk_amount = equity * Decimal(str(self._config.risk_per_trade_pct))
        qty = (risk_amount / stop_distance).quantize(_QTY_STEP)

        return BacktestTrade(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            market_type=signal.market_type,
            direction=signal.direction,
            side=side,
            entry_time=bar.open_time,
            entry_price=entry_price,
            quantity=qty,
            stop_price=stop,
            target_price=signal.suggested_target,
        )

    def _check_exit(
        self, trade: BacktestTrade, bar: Candle
    ) -> tuple[str | None, Decimal | None]:
        if trade.direction == SignalDirection.LONG:
            if bar.low <= trade.stop_price:
                return "stop", trade.stop_price
            if trade.target_price is not None and bar.high >= trade.target_price:
                return "target", trade.target_price
        else:
            if bar.high >= trade.stop_price:
                return "stop", trade.stop_price
            if trade.target_price is not None and bar.low <= trade.target_price:
                return "target", trade.target_price
        return None, None

    def _close(
        self,
        trade: BacktestTrade,
        exit_time,
        exit_price_raw: Decimal,
        reason: str,
    ) -> tuple[BacktestTrade, Decimal, Decimal]:
        exit_price = self._apply_slippage(exit_price_raw, trade.side, entry=False)
        qty = trade.quantity

        if trade.direction == SignalDirection.LONG:
            gross = (exit_price - trade.entry_price) * qty
        else:
            gross = (trade.entry_price - exit_price) * qty

        entry_notional = trade.entry_price * qty
        exit_notional = exit_price * qty
        fees = (entry_notional + exit_notional) * self._fee_ratio
        net_pnl = gross - fees

        return_pct = (
            float(net_pnl / entry_notional * 100) if entry_notional > 0 else 0.0
        )

        closed = trade.model_copy(
            update={
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": reason,
                "pnl": net_pnl,
                "fees": fees,
                "return_pct": return_pct,
            }
        )
        return closed, net_pnl, fees

    def _apply_slippage(
        self, price: Decimal, side: OrderSide, *, entry: bool
    ) -> Decimal:
        if entry:
            factor = (
                Decimal(1) + self._slip_ratio
                if side == OrderSide.BUY
                else Decimal(1) - self._slip_ratio
            )
        else:
            factor = (
                Decimal(1) - self._slip_ratio
                if side == OrderSide.BUY
                else Decimal(1) + self._slip_ratio
            )
        return (price * factor).quantize(_QTY_STEP)