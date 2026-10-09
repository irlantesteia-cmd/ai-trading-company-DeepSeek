from __future__ import annotations

import asyncio
import inspect
import logging
from decimal import Decimal
from uuid import uuid4

from app.agents.base import BaseAgent
from app.core.config import settings
from app.core.enums import AgentRole, MarketType, OrderSide, OrderStatus, OrderType, PositionSide
from app.domain.models.order import Order, OrderRequest
from app.domain.models.order_intent import OrderIntent
from app.events.event import (
    HealthCheckFailed,
    OrderFilled,
    OrderIntentCreated,
    OrderSubmitted,
)
from app.exchanges.symbol_info import SymbolInfoService

logger = logging.getLogger(__name__)


class ExecutionAgent(BaseAgent):
    role = AgentRole.EXECUTION
    name = "execution"

    def __init__(self, context, *, symbol_info: SymbolInfoService | None = None) -> None:
        super().__init__(context)
        self._symbol_info = symbol_info or SymbolInfoService()

    @property
    def symbol_info(self) -> SymbolInfoService:
        return self._symbol_info

    async def execute(self, intent: OrderIntent) -> Order:
        await self.context.event_bus.publish(OrderIntentCreated(
            intent_id=intent.intent_id, signal_id=intent.signal_id,
            symbol=intent.symbol, side=intent.side.value,
            quantity=float(intent.quantity),
        ))

        quantity = self._quantize_quantity(intent)
        if quantity <= 0:
            raise ValueError(
                f"quantity após quantização ficou <= 0 "
                f"(original={intent.quantity}, symbol={intent.symbol})"
            )

        stop_price = intent.stop_price
        if stop_price is not None:
            stop_price = self._symbol_info.round_price(
                intent.symbol, intent.market_type, stop_price
            )

        request = OrderRequest(
            client_order_id=intent.client_order_id,
            symbol=intent.symbol, market_type=intent.market_type,
            side=intent.side, type=intent.order_type,
            quantity=quantity, price=intent.price, stop_price=stop_price,
        )

        order = await self.context.exchange.orders.place_order(request)

        await self.context.event_bus.publish(OrderSubmitted(
            client_order_id=request.client_order_id, symbol=request.symbol,
        ))

        if order.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
            order = await self._wait_for_fill(order)

        if order.fills and order.average_price is not None:
            await self.context.event_bus.publish(OrderFilled(
                exchange_order_id=order.exchange_order_id,
                symbol=order.symbol,
                filled_quantity=float(order.executed_quantity),
                average_price=float(order.average_price),
                order=order,
            ))
            logger.info("execution.ordered_and_filled", extra={
                "order_id": order.exchange_order_id, "symbol": order.symbol,
                "num_fills": len(order.fills),
                "executed_quantity": str(order.executed_quantity),
                "average_price": str(order.average_price),
            })

            if (
                order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
                and (settings.stop_loss_enabled or settings.take_profit_enabled)
            ):
                await self._place_protective_orders(intent, order)
        else:
            logger.info("execution.ordered_pending_fills", extra={
                "order_id": order.exchange_order_id, "symbol": order.symbol,
                "status": order.status.value,
            })
        return order

    def _quantize_quantity(self, intent: OrderIntent) -> Decimal:
        original = intent.quantity
        rounded = self._symbol_info.round_quantity(
            intent.symbol, intent.market_type, original
        )
        if rounded != original:
            logger.info("execution.quantity_rounded", extra={
                "symbol": intent.symbol, "original": str(original), "rounded": str(rounded),
            })
        return rounded

    async def _wait_for_fill(self, order: Order) -> Order:
        for attempt in range(settings.order_fill_poll_attempts):
            await asyncio.sleep(settings.order_fill_poll_interval_s)
            try:
                updated = await self.context.exchange.orders.get_order(
                    order.symbol, order.exchange_order_id, order.market_type,
                )
            except Exception:
                logger.exception("execution.fill_poll_failed", extra={
                    "order_id": order.exchange_order_id, "attempt": attempt,
                })
                continue
            if updated.fills:
                return updated
            if updated.status not in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                return updated
        return order

    # ------------------------------------------------ protective orders (SL/TP)
    async def _place_protective_orders(
        self,
        intent: OrderIntent,
        entry: Order,
        *,
        send_sl: bool | None = None,
        send_tp: bool | None = None,
    ) -> None:
        """Envia STOP_MARKET (SL) e/ou TAKE_PROFIT_MARKET (TP) após fill.

        Usa `/fapi/v1/algoOrder` (Algo Order API): a partir de 2025 a
        Binance rejeita STOP_MARKET/TAKE_PROFIT_MARKET em `/fapi/v1/order`
        com `-4120`.
        """
        if intent.market_type is not MarketType.FUTURES:
            return

        if send_sl is None:
            send_sl = settings.stop_loss_enabled
        if send_tp is None:
            send_tp = settings.take_profit_enabled

        if not (send_sl or send_tp):
            logger.info("execution.protection_disabled", extra={"symbol": intent.symbol})
            return

        is_hedge = await self._detect_hedge_mode()
        exit_side = OrderSide.SELL if entry.side is OrderSide.BUY else OrderSide.BUY

        if is_hedge:
            position_side: PositionSide | None = (
                getattr(intent, "position_side", None)
                or self._infer_position_side(entry.side)
            )
        else:
            position_side = None

        stop_price = self._anchor_to_fill(intent.stop_price, intent, entry)
        target_price = self._anchor_to_fill(
            getattr(intent, "target_price", None), intent, entry
        )

        if send_sl and stop_price is not None:
            sl_ok = await self._send_protective_order(
                symbol=intent.symbol,
                side=exit_side,
                type_=OrderType.STOP_MARKET,
                trigger_price=stop_price,
                quantity=entry.executed_quantity,
                position_side=position_side,
                is_hedge=is_hedge,
                kind="stop_loss",
            )
            if not sl_ok and settings.close_on_stop_loss_failure:
                # Sem stop, a posição não pode ficar aberta. O TP não é enviado.
                await self._flatten_after_stop_loss_failure(intent, position_side)
                return

        if send_tp and target_price is not None:
            await self._send_protective_order(
                symbol=intent.symbol,
                side=exit_side,
                type_=OrderType.TAKE_PROFIT_MARKET,
                trigger_price=target_price,
                quantity=entry.executed_quantity,
                position_side=position_side,
                is_hedge=is_hedge,
                kind="take_profit",
            )

    @staticmethod
    def _anchor_to_fill(
        level: Decimal | None, intent: OrderIntent, entry: Order
    ) -> Decimal | None:
        """Reposiciona SL/TP no preço real do fill, mantendo a distância que o
        dimensionamento usou (a partir de `intent.reference_price`).

        O sinal é calculado no close do candle; a ordem MARKET sai a outro
        preço (mercado andou, derrapagem). Sem isso, o SL pode já estar do
        lado errado do preço e a Binance recusa (`-2021`).
        """
        reference = intent.reference_price
        fill = entry.average_price
        if level is None or reference is None or fill is None:
            return level
        anchored = fill + (level - reference)
        if anchored <= 0:
            return level
        if anchored != level:
            logger.info("execution.protective_level_anchored", extra={
                "symbol": intent.symbol,
                "reference_price": str(reference),
                "fill_price": str(fill),
                "original": str(level),
                "anchored": str(anchored),
            })
        return anchored

    async def _flatten_after_stop_loss_failure(
        self, intent: OrderIntent, position_side: PositionSide | None
    ) -> None:
        logger.error("execution.flattening_after_stop_loss_failure", extra={
            "symbol": intent.symbol, "signal_id": intent.signal_id,
        })
        try:
            order = await self.context.exchange.positions.close_position(
                intent.symbol, position_side
            )
        except Exception:
            logger.exception("execution.flatten_failed", extra={
                "symbol": intent.symbol,
                "hint": "posição SEM stop-loss: fechar manualmente (close_all_positions.py)",
            })
            await self.context.event_bus.publish(HealthCheckFailed(
                component="protection",
                detail=f"{intent.symbol}: stop-loss falhou e o fechamento também falhou",
            ))
            return

        await self.context.event_bus.publish(HealthCheckFailed(
            component="protection",
            detail=f"{intent.symbol}: stop-loss falhou; posição fechada a mercado",
        ))
        if order is not None and order.fills and order.average_price is not None:
            await self.context.event_bus.publish(OrderFilled(
                exchange_order_id=order.exchange_order_id,
                symbol=order.symbol,
                filled_quantity=float(order.executed_quantity),
                average_price=float(order.average_price),
                order=order,
            ))

    async def _send_protective_order(
        self,
        *,
        symbol: str,
        side: OrderSide,
        type_: OrderType,
        trigger_price: Decimal,
        quantity: Decimal,
        position_side: PositionSide | None,
        is_hedge: bool,
        kind: str,
    ) -> bool:
        """Envia a ordem condicional. Retorna False se não foi criada."""
        try:
            rounded_trigger = self._symbol_info.round_price(
                symbol, MarketType.FUTURES, trigger_price
            )
            rounded_qty = self._symbol_info.round_quantity(
                symbol, MarketType.FUTURES, quantity
            )
        except Exception:
            logger.exception(f"execution.{kind}_quantize_failed", extra={
                "symbol": symbol, "trigger_price": str(trigger_price),
            })
            return False

        prefix = "sl" if kind == "stop_loss" else "tp"
        request = OrderRequest(
            client_order_id=f"{prefix}-{uuid4().hex[:16]}",
            symbol=symbol,
            market_type=MarketType.FUTURES,
            side=side,
            type=type_,
            quantity=rounded_qty,
            stop_price=rounded_trigger,
            close_position=not is_hedge,
            position_side=position_side if is_hedge else None,
        )
        try:
            protective = await self.context.exchange.orders.place_conditional_order(request)
            logger.info(f"execution.{kind}_placed", extra={
                "order_id": protective.exchange_order_id,
                "symbol": symbol,
                "trigger_price": str(rounded_trigger),
                "quantity": str(rounded_qty),
                "hedge_mode": is_hedge,
            })
        except Exception:
            logger.exception(f"execution.{kind}_failed", extra={
                "symbol": symbol,
                "trigger_price": str(rounded_trigger),
                "quantity": str(rounded_qty),
                "hedge_mode": is_hedge,
            })
            return False
        return True

    async def _detect_hedge_mode(self) -> bool:
        """Detecta hedge mode do provider de posições.

        Aceita `bool`, `awaitable[bool]` ou `AsyncMock`. Qualquer outra coisa
        (ex.: `MagicMock` mal configurado) → `False` (one-way, default seguro).
        """
        provider = self.context.exchange.positions
        method = getattr(provider, "is_hedge_mode", None)
        if method is None:
            return False
        try:
            result = method()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("execution.hedge_mode_detection_failed")
            return False

        if not isinstance(result, bool):
            logger.warning(
                "execution.hedge_mode_unexpected_type",
                extra={"type": type(result).__name__},
            )
            return False
        return result

    @staticmethod
    def _infer_position_side(entry_side: OrderSide) -> PositionSide:
        return PositionSide.LONG if entry_side is OrderSide.BUY else PositionSide.SHORT