from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from app.core.enums import OrderSide, OrderType, SignalDirection
from app.domain.models.order_intent import OrderIntent
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)

_QTY_STEP = Decimal("0.00000001")  # 8 casas — chão seguro; a Binance arredonda ao step real


class PositionSizer:
    """Dimensiona a ordem por risco-por-trade.

        qty = (equity * risk_per_trade_pct) / |entry - stop|

    Aplicado ainda um teto de notional por símbolo:
        qty <= max_notional_per_symbol / entry

    `suggested_stop` ausente → usa `default_stop_pct` sobre `entry`.
    """

    def __init__(
        self,
        *,
        risk_per_trade_pct: float = 0.01,
        default_stop_pct: float = 0.02,
        max_notional_per_symbol: Decimal = Decimal(10000),
    ) -> None:
        self._risk_pct = Decimal(str(risk_per_trade_pct))
        self._default_stop_pct = Decimal(str(default_stop_pct))
        self._max_notional = max_notional_per_symbol

    @property
    def risk_pct(self) -> Decimal:
        return self._risk_pct

    @property
    def max_notional(self) -> Decimal:
        return self._max_notional

    def size(
        self,
        signal: Signal,
        decision: RiskDecision,
        state: PortfolioState,
    ) -> OrderIntent:
        if decision.action.value != "APPROVE":
            raise ValueError(f"sizing requer decision APPROVE, got {decision.action}")

        if signal.suggested_entry is None:
            raise ValueError("Signal sem suggested_entry")

        entry = signal.suggested_entry
        if entry <= 0:
            raise ValueError(f"entry inválido: {entry}")

        if signal.suggested_stop is not None:
            stop = signal.suggested_stop
        else:
            delta = entry * self._default_stop_pct
            stop = entry - delta if signal.direction == SignalDirection.LONG else entry + delta

        stop_distance = abs(entry - stop)
        if stop_distance == 0:
            raise ValueError("stop == entry; impossível dimensionar")

        risk_amount = state.equity * self._risk_pct
        raw_qty = risk_amount / stop_distance

        max_qty_by_notional = self._max_notional / entry
        quantity = min(raw_qty, max_qty_by_notional)
        quantity = quantity.quantize(_QTY_STEP, rounding=ROUND_DOWN)

        if quantity <= 0:
            raise ValueError(
                f"quantidade calculada <= 0 (raw={raw_qty}, cap={max_qty_by_notional})"
            )

        side = (
            OrderSide.BUY
            if signal.direction == SignalDirection.LONG
            else OrderSide.SELL
        )

        logger.info(
            "sizing.computed",
            extra={
                "symbol": signal.symbol,
                "equity": str(state.equity),
                "entry": str(entry),
                "stop": str(stop),
                "risk_amount": str(risk_amount),
                "quantity": str(quantity),
            },
        )

        return OrderIntent(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            market_type=signal.market_type,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            stop_price=stop,
            reason=(
                f"risk_pct={self._risk_pct} entry={entry} stop={stop} "
                f"max_notional={self._max_notional}"
            ),
            agent="portfolio_manager",
        )