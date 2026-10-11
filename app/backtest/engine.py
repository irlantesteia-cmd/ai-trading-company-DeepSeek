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
_BPS = Decimal(10000)


class BacktestEngine:
    """Backtest determinístico barra-a-barra, com as regras do bot ao vivo.

    Convenções:
      - A estratégia recebe os últimos `lookback` candles até a barra i (como
        o AssetAgent ao vivo); sinal no fechamento de i → entrada na abertura
        de i+1.
      - SL/TP reancorados no preço de entrada (`anchor_to_fill`), mantendo a
        distância do sinal, como o ExecutionAgent faz após o fill.
      - Stop/target checados a partir da barra de entrada (low/high). Se os
        dois são atingíveis na mesma barra, o stop vence (pessimista).
      - Saída por tempo (`max_holding_bars`): fecha no close da N-ésima barra
        desde a entrada (contando a de entrada).
      - Derrapagem contra a posição; taxas sobre o notional de entrada e de
        saída (taxa de entrada e de saída podem diferir: maker vs taker).
      - Tamanho: risco % do equity / distância do stop, limitado por
        `max_position_notional` (como o PositionSizer).
      - Uma posição por vez; ao final, posição aberta fecha no último close.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()
        cfg = self._config
        self._entry_fee = Decimal(str(cfg.entry_fee_bps if cfg.entry_fee_bps is not None else cfg.fee_bps)) / _BPS
        self._exit_fee = Decimal(str(cfg.exit_fee_bps if cfg.exit_fee_bps is not None else cfg.fee_bps)) / _BPS
        self._slip_ratio = Decimal(str(cfg.slippage_bps)) / _BPS

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

        for i, bar in enumerate(candles):
            # 1) Entra o sinal pendente na abertura desta barra
            if pending is not None and open_trade is None:
                open_trade = self._enter(pending, bar, equity)
                pending = None

            # 2) Saída: stop/alvo dentro da barra, ou por tempo no close
            if open_trade is not None:
                open_trade = open_trade.model_copy(
                    update={"bars_held": open_trade.bars_held + 1}
                )
                reason, exit_price = self._check_exit(open_trade, bar)
                if reason is None and 0 < cfg.max_holding_bars <= open_trade.bars_held:
                    reason, exit_price = "time", bar.close
                if reason is not None and exit_price is not None:
                    closed, net_pnl = self._close(open_trade, bar.close_time, exit_price, reason)
                    trades.append(closed)
                    equity += net_pnl
                    equity_curve.append((bar.close_time, equity))
                    open_trade = None

            # 3) Novo sinal (apenas sem posição)
            if open_trade is None and i < len(candles) - 1:
                start = 0 if cfg.lookback is None else max(0, i + 1 - cfg.lookback)
                ctx = StrategyContext(
                    symbol=symbol,
                    market_type=market_type,
                    interval=interval,
                    candles=candles[start : i + 1],
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
            closed, net_pnl = self._close(open_trade, last.close_time, last.close, "end")
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
        target = signal.suggested_target
        assert stop is not None  # filtrado em run()
        if self._config.anchor_to_fill and signal.suggested_entry is not None:
            shift = entry_price - signal.suggested_entry
            stop = stop + shift
            target = target + shift if target is not None else None

        stop_distance = abs(entry_price - stop)
        if stop_distance <= 0:
            raise ValueError(f"stop_distance inválido: {stop_distance}")

        risk_amount = equity * Decimal(str(self._config.risk_per_trade_pct))
        qty = risk_amount / stop_distance
        cap = self._config.max_position_notional
        if cap is not None:
            qty = min(qty, cap / entry_price)
        qty = qty.quantize(_QTY_STEP)

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
            target_price=target,
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
    ) -> tuple[BacktestTrade, Decimal]:
        exit_price = self._apply_slippage(exit_price_raw, trade.side, entry=False)
        qty = trade.quantity
        long = trade.direction == SignalDirection.LONG

        gross = (exit_price - trade.entry_price) * qty if long else (trade.entry_price - exit_price) * qty
        entry_notional = trade.entry_price * qty
        exit_notional = exit_price * qty
        fees = entry_notional * self._entry_fee + exit_notional * self._exit_fee
        net_pnl = gross - fees

        # Bruto = movimento de preço sem derrapagem nem taxas.
        raw_entry = self._remove_slippage(trade.entry_price, trade.side)
        raw_move = (exit_price_raw - raw_entry) if long else (raw_entry - exit_price_raw)

        closed = trade.model_copy(
            update={
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": reason,
                "pnl": net_pnl,
                "fees": fees,
                "return_pct": float(net_pnl / entry_notional * 100) if entry_notional > 0 else 0.0,
                "gross_return_pct": float(raw_move / raw_entry * 100) if raw_entry > 0 else 0.0,
            }
        )
        return closed, net_pnl

    def _slip_factor(self, side: OrderSide, *, entry: bool) -> Decimal:
        # Entrada compra (ou saída de short) paga a mais; venda recebe a menos.
        pays_more = (side == OrderSide.BUY) == entry
        return Decimal(1) + self._slip_ratio if pays_more else Decimal(1) - self._slip_ratio

    def _apply_slippage(self, price: Decimal, side: OrderSide, *, entry: bool) -> Decimal:
        return price * self._slip_factor(side, entry=entry)

    def _remove_slippage(self, price: Decimal, side: OrderSide) -> Decimal:
        return price / self._slip_factor(side, entry=True)
