from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from app.core.enums import OrderSide, OrderType, SignalDirection
from app.domain.models.order_intent import OrderIntent
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)
_QTY_STEP = Decimal("0.00000001")


class PositionSizer:
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

    def size(self, signal: Signal, decision: RiskDecision, state: PortfolioState) -> OrderIntent:
        if decision.action.value != "APPROVE":
            raise ValueError(f"sizing requer decision APPROVE, got {decision.action}")
        if state.equity <= 0:
            raise ValueError(f"equity do portfólio é {state.equity}; verifique o saldo")
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
            raise ValueError("stop == entry")

        risk_amount = state.equity * self._risk_pct
        raw_qty = risk_amount / stop_distance
        max_qty = self._max_notional / entry
        quantity = min(raw_qty, max_qty).quantize(_QTY_STEP, rounding=ROUND_DOWN)
        if quantity <= 0:
            raise ValueError(f"quantidade <= 0 (raw={raw_qty}, cap={max_qty})")

        side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
        return OrderIntent(
            signal_id=signal.signal_id, symbol=signal.symbol,
            market_type=signal.market_type, side=side,
            quantity=quantity, order_type=OrderType.MARKET, stop_price=stop,
            target_price=signal.suggested_target,
            reference_price=entry,
            reason=f"risk_pct={self._risk_pct} entry={entry} stop={stop}",
            agent="portfolio_manager",
        )