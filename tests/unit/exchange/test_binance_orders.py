from decimal import Decimal

import pytest
import respx
from httpx import Response

from app.core.enums import (
    MarketType,
    OrderSide,
    OrderType,
    PositionSide,
    TimeInForce,
)
from app.domain.models.order import OrderRequest
from app.exchanges.binance.client import SPOT_REST_DEMO, BinanceClient
from app.exchanges.binance.orders import BinanceOrderProvider

FIXED_SERVER_TIME_MS = 1_700_000_000_000


def _provider() -> tuple[BinanceOrderProvider, BinanceClient]:
    client = BinanceClient(api_key="k", api_secret="s", testnet=True)
    return BinanceOrderProvider(client), client


def _order_json(**overrides) -> dict:
    base = {
        "orderId": 1,
        "clientOrderId": "c-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "status": "NEW",
        "origQty": "1.0",
        "executedQty": "0",
        "price": "60000",
        "timeInForce": "GTC",
        "time": 1_700_000_000_000,
        "updateTime": 1_700_000_000_000,
    }
    base.update(overrides)
    return base


def _mock_spot_time(mock) -> None:
    mock.get("/api/v3/time").mock(
        return_value=Response(200, json={"serverTime": FIXED_SERVER_TIME_MS})
    )


def _mock_futures_time(mock) -> None:
    mock.get("/fapi/v1/time").mock(
        return_value=Response(200, json={"serverTime": FIXED_SERVER_TIME_MS})
    )


@pytest.mark.asyncio
async def test_place_order_spot():
    provider, client = _provider()
    try:
        with respx.mock(base_url=SPOT_REST_DEMO) as mock:
            _mock_spot_time(mock)
            mock.post("/api/v3/order").mock(
                return_value=Response(200, json=_order_json())
            )
            order = await provider.place_order(
                OrderRequest(
                    client_order_id="c-1",
                    symbol="BTCUSDT",
                    market_type=MarketType.SPOT,
                    side=OrderSide.BUY,
                    type=OrderType.LIMIT,
                    quantity=Decimal("1.0"),
                    price=Decimal(60000),
                    time_in_force=TimeInForce.GTC,
                )
            )
            assert order.exchange_order_id == "1"
            assert order.symbol == "BTCUSDT"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancel_order_spot():
    provider, client = _provider()
    try:
        with respx.mock(base_url=SPOT_REST_DEMO) as mock:
            _mock_spot_time(mock)
            mock.delete("/api/v3/order").mock(
                return_value=Response(200, json=_order_json(status="CANCELED"))
            )
            order = await provider.cancel_order("BTCUSDT", "1", MarketType.SPOT)
            assert order.status == "CANCELED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_list_open_orders_spot():
    provider, client = _provider()
    try:
        with respx.mock(base_url=SPOT_REST_DEMO) as mock:
            _mock_spot_time(mock)
            mock.get("/api/v3/openOrders").mock(
                return_value=Response(200, json=[_order_json()])
            )
            orders = await provider.list_open_orders("BTCUSDT", MarketType.SPOT)
            assert len(orders) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_futures_reduce_only():
    provider, client = _provider()
    try:
        with respx.mock(base_url="https://demo-fapi.binance.com") as mock:
            _mock_futures_time(mock)
            route = mock.post("/fapi/v1/order").mock(
                return_value=Response(
                    200,
                    json=_order_json(
                        type="MARKET",
                        side="SELL",
                        reduceOnly=True,
                        positionSide="LONG",
                    ),
                )
            )
            order = await provider.place_order(
                OrderRequest(
                    client_order_id="c-2",
                    symbol="BTCUSDT",
                    market_type=MarketType.FUTURES,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    quantity=Decimal("0.5"),
                    reduce_only=True,
                    position_side=PositionSide.LONG,
                )
            )
            assert order.reduce_only is True
            sent = route.calls[0].request
            assert "reduceOnly=true" in str(sent.url)
    finally:
        await client.close()