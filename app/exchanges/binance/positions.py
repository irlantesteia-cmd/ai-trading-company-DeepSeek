from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from app.core.config import settings
from app.core.enums import (
    MarginType,
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from app.core.exceptions import ExchangeError
from app.domain.models.order import Order, OrderRequest
from app.exchanges.base.positions import PositionProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import map_futures_position
from app.exchanges.binance.orders import BinanceOrderProvider

logger = logging.getLogger(__name__)


class BinancePositionProvider(PositionProvider):
    def __init__(self, client: BinanceClient, orders: BinanceOrderProvider) -> None:
        self._client = client
        self._orders = orders
        self._is_hedge_mode: bool | None = None

    async def get_position(self, symbol: str, position_side: PositionSide | None = None):
        raw = await self._client.request(
            "GET", "/fapi/v2/positionRisk",
            market_type=MarketType.FUTURES, signed=True, params={"symbol": symbol},
        )
        for entry in raw:
            pos = map_futures_position(entry)
            if not pos.is_open:
                continue
            if position_side is None or pos.position_side == position_side:
                return pos
        return None

    async def is_hedge_mode(self) -> bool:
        if self._is_hedge_mode is None:
            response = await self._client.request(
                "GET", "/fapi/v1/positionSide/dual",
                market_type=MarketType.FUTURES, signed=True,
            )
            self._is_hedge_mode = bool(response.get("dualSidePosition", False))
            logger.info("positions.mode_detected", extra={
                "hedge_mode": self._is_hedge_mode, "raw": response,
            })
        return self._is_hedge_mode

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._client.request(
            "POST", "/fapi/v1/leverage",
            market_type=MarketType.FUTURES, signed=True,
            params={"symbol": symbol, "leverage": leverage},
        )

    async def set_margin_type(self, symbol: str, margin_type: MarginType) -> None:
        try:
            await self._client.request(
                "POST", "/fapi/v1/marginType",
                market_type=MarketType.FUTURES, signed=True,
                params={"symbol": symbol, "marginType": margin_type.value},
            )
        except ExchangeError as exc:
            if "-4046" in str(exc):
                return
            raise

    async def close_position(
        self, symbol: str, position_side: PositionSide | None = None
    ) -> Order | None:
        pos = await self.get_position(symbol, position_side)
        if pos is None:
            return None

        # Cancela ordens condicionais (SL/TP) pendentes antes de fechar —
        # evita órfãs que disparariam contra uma posição já encerrada.
        if settings.cancel_protective_orders_on_close:
            try:
                canceled = await self._orders.cancel_all_algo_orders(symbol)
                if canceled:
                    logger.info(
                        "positions.canceled_protective_orders",
                        extra={"symbol": symbol, "count": canceled},
                    )
            except Exception:
                logger.exception(
                    "positions.cancel_protective_orders_failed",
                    extra={"symbol": symbol},
                )

        side = OrderSide.SELL if pos.position_side == PositionSide.LONG else OrderSide.BUY
        is_hedge = await self.is_hedge_mode()

        if is_hedge:
            reduce_only = False
            outgoing_side: PositionSide | None = pos.position_side
        else:
            reduce_only = True
            outgoing_side = None

        logger.info("positions.closing", extra={
            "symbol": symbol, "position_side": pos.position_side.value,
            "quantity": str(pos.quantity), "hedge_mode": is_hedge, "reduce_only": reduce_only,
        })

        order = await self._orders.place_order(OrderRequest(
            client_order_id=f"close-{uuid4().hex[:16]}",
            symbol=symbol, market_type=MarketType.FUTURES,
            side=side, type=OrderType.MARKET, quantity=pos.quantity,
            reduce_only=reduce_only, position_side=outgoing_side,
        ))

        # MARKET reduce-only pode não trazer `fills` na resposta inicial
        # (diferente de entradas, que o `ExecutionAgent._wait_for_fill` cobre).
        # Sem fills anexados, o `RoundTripAgent` nunca é notificado e o
        # `round_trip` fica OPEN mesmo após a posição fechar. Buscamos via
        # `/fapi/v1/userTrades`, mesmo padrão do `ExecutionAgent`.
        if not order.fills:
            order = await self._poll_fills(order)

        return order

    async def _poll_fills(self, order: Order) -> Order:
        for attempt in range(settings.order_fill_poll_attempts):
            await asyncio.sleep(settings.order_fill_poll_interval_s)
            try:
                updated = await self._orders.get_order(
                    order.symbol, order.exchange_order_id, order.market_type,
                )
            except Exception:
                logger.exception(
                    "positions.fill_poll_failed",
                    extra={"order_id": order.exchange_order_id, "attempt": attempt},
                )
                continue
            if updated.fills:
                return updated
            if updated.status not in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                return updated
        return order