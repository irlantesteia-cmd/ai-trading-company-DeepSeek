from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from app.core.enums import MarketType, OrderStatus
from app.core.exceptions import ExchangeError, ExchangeOrderRejectedError
from app.domain.models.order import Order, OrderRequest
from app.exchanges.base.orders import OrderProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import (
    map_algo_order,
    map_order,
    map_user_trade,
    order_request_to_algo_params,
    order_request_to_params,
)

logger = logging.getLogger(__name__)

_ALGO_PATH_FUTURES = "/fapi/v1/algoOrder"
_OPEN_ALGO_ORDERS_PATH_FUTURES = "/fapi/v1/openAlgoOrders"


class BinanceOrderProvider(OrderProvider):
    def __init__(self, client: BinanceClient) -> None:
        self._client = client

    # --------------------------------------------------------------- paths
    @staticmethod
    def _paths(market_type: MarketType) -> dict[str, str]:
        if market_type is MarketType.SPOT:
            return {
                "order": "/api/v3/order",
                "openOrders": "/api/v3/openOrders",
                "userTrades": "/api/v3/myTrades",
            }
        return {
            "order": "/fapi/v1/order",
            "openOrders": "/fapi/v1/openOrders",
            "userTrades": "/fapi/v1/userTrades",
        }

    # --------------------------------------------------------------- actions
    async def place_order(self, request: OrderRequest) -> Order:
        """Ordens normais (MARKET/LIMIT) em `/fapi/v1/order`."""
        paths = self._paths(request.market_type)
        params = order_request_to_params(
            symbol=request.symbol,
            side=request.side,
            type_=request.type,
            quantity=request.quantity,
            client_order_id=request.client_order_id,
            market_type=request.market_type,
            price=request.price,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            position_side=request.position_side,
            close_position=request.close_position,
        )
        raw = await self._client.request(
            "POST",
            paths["order"],
            market_type=request.market_type,
            signed=True,
            params=params,
        )
        return map_order(raw, request.market_type)

    async def place_conditional_order(self, request: OrderRequest) -> Order:
        """Ordens condicionais (SL/TP/trailing) em `/fapi/v1/algoOrder`."""
        if request.market_type is not MarketType.FUTURES:
            raise ValueError(
                "place_conditional_order é apenas para FUTURES "
                f"(recebido {request.market_type.value})"
            )
        if request.stop_price is None:
            raise ValueError(
                "place_conditional_order requer stop_price (triggerPrice)"
            )

        params = order_request_to_algo_params(
            symbol=request.symbol,
            side=request.side,
            type_=request.type,
            quantity=request.quantity,
            client_order_id=request.client_order_id,
            trigger_price=request.stop_price,
            reduce_only=request.reduce_only,
            position_side=request.position_side,
            close_position=request.close_position,
        )
        raw = await self._client.request(
            "POST",
            _ALGO_PATH_FUTURES,
            market_type=MarketType.FUTURES,
            signed=True,
            params=params,
        )

        algo_id = raw.get("algoId") if isinstance(raw, dict) else None
        algo_status = raw.get("algoStatus") if isinstance(raw, dict) else None
        if not algo_id or algo_status not in ("NEW", "TRIGGERED", "FINISHED"):
            code = raw.get("code") if isinstance(raw, dict) else None
            msg = raw.get("msg") if isinstance(raw, dict) else None
            raise ExchangeOrderRejectedError(
                f"algoOrder rejeitada: {code}: {msg} (payload={raw!r})"
            )

        now = datetime.now(UTC)
        return Order(
            exchange_order_id=str(algo_id),
            client_order_id=raw.get("clientAlgoId", request.client_order_id),
            symbol=request.symbol,
            market_type=request.market_type,
            side=request.side,
            type=request.type,
            status=OrderStatus.NEW,
            quantity=request.quantity,
            executed_quantity=Decimal(0),
            price=request.price,
            average_price=None,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            position_side=request.position_side,
            created_at=now,
            updated_at=now,
            fills=[],
        )

    async def list_open_algo_orders(self, symbol: str | None = None) -> list[Order]:
        """Lista ordens condicionais abertas (Algo Order API).

        Isolamento por ordem: uma payload malformada é logada e ignorada,
        sem derrubar o listing inteiro. Retorna lista vazia em caso de
        erro no request.
        """
        params: dict = {}
        if symbol:
            params["symbol"] = symbol
        try:
            raw = await self._client.request(
                "GET",
                _OPEN_ALGO_ORDERS_PATH_FUTURES,
                market_type=MarketType.FUTURES,
                signed=True,
                params=params,
            )
        except ExchangeError as exc:
            logger.warning(
                "binance.list_open_algo_orders_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return []
        except Exception:
            logger.exception(
                "binance.list_open_algo_orders_error",
                extra={"symbol": symbol},
            )
            return []

        if isinstance(raw, list):
            orders_raw = raw
        elif isinstance(raw, dict):
            orders_raw = raw.get("orders") or []
        else:
            orders_raw = []

        orders: list[Order] = []
        for o in orders_raw:
            try:
                orders.append(map_algo_order(o))
            except Exception:
                logger.exception(
                    "binance.list_open_algo_orders_parse_failed",
                    extra={"symbol": symbol, "raw": str(o)[:200]},
                )
        return orders

    async def list_open_conditional_orders(self, symbol: str | None) -> list[Order]:
        return await self.list_open_algo_orders(symbol)

    async def get_conditional_order(self, symbol: str, exchange_order_id: str) -> Order:
        """Consulta uma ordem algo pelo `algoId` (`GET /fapi/v1/algoOrder`)."""
        raw = await self._client.request(
            "GET",
            _ALGO_PATH_FUTURES,
            market_type=MarketType.FUTURES,
            signed=True,
            params={"symbol": symbol, "algoId": exchange_order_id},
        )
        return map_algo_order(raw)

    async def cancel_algo_order(self, symbol: str, algo_id: str) -> None:
        """Cancela uma ordem condicional via `DELETE /fapi/v1/algoOrder`."""
        await self._client.request(
            "DELETE",
            _ALGO_PATH_FUTURES,
            market_type=MarketType.FUTURES,
            signed=True,
            params={"symbol": symbol, "algoId": algo_id},
        )

    async def cancel_all_algo_orders(self, symbol: str) -> int:
        """Cancela todas as ordens condicionais abertas para um símbolo."""
        orders = await self.list_open_algo_orders(symbol)
        if not orders:
            return 0

        canceled = 0
        for order in orders:
            try:
                await self.cancel_algo_order(order.symbol, order.exchange_order_id)
                canceled += 1
                logger.info(
                    "binance.algo_order_canceled",
                    extra={
                        "symbol": order.symbol,
                        "algo_id": order.exchange_order_id,
                        "client_algo_id": order.client_order_id,
                        "type": order.type.value,
                    },
                )
            except Exception:
                logger.exception(
                    "binance.cancel_algo_order_failed",
                    extra={
                        "symbol": order.symbol,
                        "algo_id": order.exchange_order_id,
                    },
                )
        return canceled

    async def cancel_order(
        self,
        symbol: str,
        exchange_order_id: str,
        market_type: MarketType,
    ) -> Order:
        paths = self._paths(market_type)
        raw = await self._client.request(
            "DELETE",
            paths["order"],
            market_type=market_type,
            signed=True,
            params={"symbol": symbol, "orderId": exchange_order_id},
        )
        return map_order(raw, market_type)

    async def get_order(
        self,
        symbol: str,
        exchange_order_id: str,
        market_type: MarketType,
    ) -> Order:
        paths = self._paths(market_type)
        raw = await self._client.request(
            "GET",
            paths["order"],
            market_type=market_type,
            signed=True,
            params={"symbol": symbol, "orderId": exchange_order_id},
        )
        order = map_order(raw, market_type)

        if (
            market_type is MarketType.FUTURES
            and not order.fills
            and order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
        ):
            order = await self._attach_fills(order, exchange_order_id)

        return order

    async def _attach_fills(self, order: Order, exchange_order_id: str) -> Order:
        paths = self._paths(order.market_type)
        try:
            trades = await self._client.request(
                "GET",
                paths["userTrades"],
                market_type=order.market_type,
                signed=True,
                params={"symbol": order.symbol, "orderId": exchange_order_id},
            )
        except Exception:
            logger.exception(
                "binance.user_trades_fetch_failed",
                extra={
                    "symbol": order.symbol,
                    "order_id": exchange_order_id,
                    "market_type": order.market_type.value,
                },
            )
            return order

        if not trades:
            logger.warning(
                "binance.user_trades_empty",
                extra={
                    "symbol": order.symbol,
                    "order_id": exchange_order_id,
                    "status": order.status.value,
                    "executed_quantity": str(order.executed_quantity),
                },
            )
            return order

        fills = [map_user_trade(t) for t in trades]
        logger.info(
            "binance.user_trades_attached",
            extra={
                "symbol": order.symbol,
                "order_id": exchange_order_id,
                "num_fills": len(fills),
            },
        )
        return order.model_copy(update={"fills": fills})

    async def list_open_orders(
        self,
        symbol: str | None,
        market_type: MarketType,
    ) -> list[Order]:
        paths = self._paths(market_type)
        params: dict = {}
        if symbol is not None:
            params["symbol"] = symbol
        raw = await self._client.request(
            "GET",
            paths["openOrders"],
            market_type=market_type,
            signed=True,
            params=params,
        )
        return [map_order(o, market_type) for o in raw]