"""Testes do fallback de fills via `/fapi/v1/userTrades` (FUTURES)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import respx

from app.core.enums import MarketType, OrderStatus
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.orders import BinanceOrderProvider

FAPI = "https://demo-fapi.binance.com"
SPOT = "https://demo-api.binance.com"


def _time_payload() -> dict:
    return {"serverTime": 1_700_000_000_000}


def _make_client() -> BinanceClient:
    return BinanceClient(
        api_key="k" * 64,
        api_secret="s" * 64,
        testnet=True,
        max_retries=0,
    )


@respx.mock
async def test_futures_get_order_attaches_fills_from_user_trades() -> None:
    respx.get(f"{FAPI}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json=_time_payload())
    )
    respx.get(f"{FAPI}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 111,
                "clientOrderId": "abc",
                "symbol": "SOLUSDT",
                "side": "SELL",
                "type": "MARKET",
                "status": "FILLED",
                "origQty": "82.12",
                "executedQty": "82.12",
                "avgPrice": "121.75",
                "price": "0",
                "time": 1_700_000_000_000,
                "updateTime": 1_700_000_000_500,
                "reduceOnly": True,
                "positionSide": "BOTH",
            },
        )
    )
    respx.get(f"{FAPI}/fapi/v1/userTrades").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "SOLUSDT",
                    "id": 42,
                    "orderId": 111,
                    "side": "SELL",
                    "price": "121.75",
                    "qty": "82.12",
                    "commission": "0.02",
                    "commissionAsset": "USDT",
                    "time": 1_700_000_000_500,
                    "positionSide": "BOTH",
                }
            ],
        )
    )

    client = _make_client()
    try:
        provider = BinanceOrderProvider(client)
        order = await provider.get_order("SOLUSDT", "111", MarketType.FUTURES)
    finally:
        await client.close()

    assert order.status is OrderStatus.FILLED
    assert len(order.fills) == 1
    assert order.fills[0].price == Decimal("121.75")
    assert order.fills[0].quantity == Decimal("82.12")
    assert order.fills[0].commission == Decimal("0.02")
    assert order.fills[0].commission_asset == "USDT"


@respx.mock
async def test_futures_get_order_with_fills_inline_skips_user_trades() -> None:
    respx.get(f"{FAPI}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json=_time_payload())
    )
    respx.get(f"{FAPI}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 222,
                "clientOrderId": "abc",
                "symbol": "SOLUSDT",
                "side": "BUY",
                "type": "MARKET",
                "status": "FILLED",
                "origQty": "1",
                "executedQty": "1",
                "avgPrice": "100",
                "price": "0",
                "time": 1_700_000_000_000,
                "updateTime": 1_700_000_000_500,
                "fills": [
                    {"price": "100", "qty": "1", "commission": "0.01",
                     "commissionAsset": "USDT"}
                ],
            },
        )
    )
    user_trades_route = respx.get(f"{FAPI}/fapi/v1/userTrades").mock(
        return_value=httpx.Response(200, json=[])
    )

    client = _make_client()
    try:
        provider = BinanceOrderProvider(client)
        order = await provider.get_order("SOLUSDT", "222", MarketType.FUTURES)
    finally:
        await client.close()

    assert len(order.fills) == 1
    assert not user_trades_route.called


@respx.mock
async def test_spot_get_order_never_calls_user_trades() -> None:
    respx.get(f"{SPOT}/api/v3/time").mock(
        return_value=httpx.Response(200, json=_time_payload())
    )
    respx.get(f"{SPOT}/api/v3/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 333,
                "clientOrderId": "abc",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "MARKET",
                "status": "FILLED",
                "origQty": "1",
                "executedQty": "1",
                "cummulativeQuoteQty": "100",
                "price": "0",
                "time": 1_700_000_000_000,
                "updateTime": 1_700_000_000_500,
                "fills": [
                    {"price": "100", "qty": "1", "commission": "0.01",
                     "commissionAsset": "USDT"}
                ],
            },
        )
    )
    spot_route = respx.get(f"{SPOT}/api/v3/myTrades").mock(
        return_value=httpx.Response(200, json=[])
    )

    client = _make_client()
    try:
        provider = BinanceOrderProvider(client)
        order = await provider.get_order("BTCUSDT", "333", MarketType.SPOT)
    finally:
        await client.close()

    assert len(order.fills) == 1
    assert not spot_route.called


@respx.mock
async def test_futures_user_trades_failure_returns_order_without_fills() -> None:
    respx.get(f"{FAPI}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json=_time_payload())
    )
    respx.get(f"{FAPI}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 444,
                "clientOrderId": "abc",
                "symbol": "SOLUSDT",
                "side": "BUY",
                "type": "MARKET",
                "status": "FILLED",
                "origQty": "1",
                "executedQty": "1",
                "avgPrice": "100",
                "price": "0",
                "time": 1_700_000_000_000,
                "updateTime": 1_700_000_000_500,
            },
        )
    )
    respx.get(f"{FAPI}/fapi/v1/userTrades").mock(
        return_value=httpx.Response(500, json={"code": -1000, "msg": "boom"})
    )

    client = _make_client()
    try:
        provider = BinanceOrderProvider(client)
        order = await provider.get_order("SOLUSDT", "444", MarketType.FUTURES)
    finally:
        await client.close()

    assert order.status is OrderStatus.FILLED
    assert order.fills == []