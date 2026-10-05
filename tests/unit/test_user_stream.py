"""Testes para o User Data Stream: mappers e helpers do cliente."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from app.core.enums import MarketType, OrderSide, OrderStatus, OrderType
from app.exchanges.binance.mappers import map_order_trade_update, map_user_trade
from app.exchanges.binance.user_stream import UserDataStreamClient


def _trade_update_payload(
    *,
    execution_type: str = "TRADE",
    order_status: str = "FILLED",
    last_qty: str = "1.5",
    trade_id: int = 42,
) -> dict:
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_700_000_000_500,
        "T": 1_700_000_000_500,
        "o": {
            "s": "SOLUSDT",
            "c": "ai-abc",
            "S": "BUY",
            "o": "MARKET",
            "f": "GTC",
            "q": "1.5",
            "p": "0",
            "ap": "122.0",
            "sp": "0",
            "x": execution_type,
            "X": order_status,
            "i": 999,
            "l": last_qty,
            "z": "1.5",
            "L": "122.0",
            "N": "USDT",
            "n": "0.05",
            "T": 1_700_000_000_500,
            "t": trade_id,
            "m": False,
            "R": False,
            "ps": "BOTH",
            "rp": "0",
        },
    }


def test_map_order_trade_update_returns_order_on_fill() -> None:
    payload = _trade_update_payload()
    order = map_order_trade_update(payload, MarketType.FUTURES)

    assert order is not None
    assert order.exchange_order_id == "999"
    assert order.symbol == "SOLUSDT"
    assert order.side is OrderSide.BUY
    assert order.type is OrderType.MARKET
    assert order.status is OrderStatus.FILLED
    assert order.executed_quantity == Decimal("1.5")
    assert order.average_price == Decimal("122.0")
    assert len(order.fills) == 1
    assert order.fills[0].quantity == Decimal("1.5")
    assert order.fills[0].price == Decimal("122.0")
    assert order.fills[0].commission == Decimal("0.05")
    assert order.fills[0].commission_asset == "USDT"
    assert order.fills[0].trade_id == "42"


def test_map_order_trade_update_returns_none_on_new() -> None:
    payload = _trade_update_payload(
        execution_type="NEW", order_status="NEW", last_qty="0"
    )
    assert map_order_trade_update(payload, MarketType.FUTURES) is None


def test_map_order_trade_update_returns_none_on_canceled() -> None:
    payload = _trade_update_payload(
        execution_type="CANCELED", order_status="CANCELED", last_qty="0"
    )
    assert map_order_trade_update(payload, MarketType.FUTURES) is None


def test_map_order_trade_update_returns_none_on_zero_last_qty() -> None:
    payload = _trade_update_payload(
        execution_type="TRADE", order_status="NEW", last_qty="0"
    )
    assert map_order_trade_update(payload, MarketType.FUTURES) is None


def test_map_order_trade_update_partially_filled() -> None:
    payload = _trade_update_payload(
        execution_type="TRADE",
        order_status="PARTIALLY_FILLED",
        last_qty="0.5",
    )
    order = map_order_trade_update(payload, MarketType.FUTURES)

    assert order is not None
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert len(order.fills) == 1
    assert order.fills[0].quantity == Decimal("0.5")


def test_map_user_trade_captures_trade_id() -> None:
    fill = map_user_trade({
        "price": "1.5",
        "qty": "100",
        "commission": "0.1",
        "commissionAsset": "USDT",
        "time": 1_700_000_000_000,
        "id": 777,
    })
    assert fill.trade_id == "777"


def test_map_user_trade_without_id_leaves_trade_id_none() -> None:
    fill = map_user_trade({
        "price": "1.5",
        "qty": "100",
        "commission": "0.1",
        "commissionAsset": "USDT",
        "time": 1_700_000_000_000,
    })
    assert fill.trade_id is None


def test_ws_url_derives_from_rest_base_demo() -> None:
    client = MagicMock()
    client.base_urls = {MarketType.FUTURES: "https://demo-fapi.binance.com"}
    uds = UserDataStreamClient(client)
    assert uds._ws_url("KEY") == "wss://demo-fstream.binance.com/ws/KEY"


def test_ws_url_derives_from_rest_base_prod() -> None:
    client = MagicMock()
    client.base_urls = {MarketType.FUTURES: "https://fapi.binance.com"}
    uds = UserDataStreamClient(client)
    assert uds._ws_url("KEY") == "wss://fstream.binance.com/ws/KEY"