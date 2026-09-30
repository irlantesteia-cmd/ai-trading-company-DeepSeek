from __future__ import annotations

from app.core.enums import MarketType
from app.domain.models.order import Order, OrderRequest
from app.exchanges.base.orders import OrderProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import map_order, order_request_to_params


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
            }
        return {
            "order": "/fapi/v1/order",
            "openOrders": "/fapi/v1/openOrders",
        }

    # --------------------------------------------------------------- actions
    async def place_order(self, request: OrderRequest) -> Order:
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
        )
        raw = await self._client.request(
            "POST",
            paths["order"],
            market_type=request.market_type,
            signed=True,
            params=params,
        )
        return map_order(raw, request.market_type)

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
        return map_order(raw, market_type)

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