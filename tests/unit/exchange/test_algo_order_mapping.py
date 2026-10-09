"""Ordens da Algo Order API com payloads reais da conta demo (2026-10-09)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType, OrderStatus, OrderType
from app.exchanges.binance.mappers import map_algo_order
from app.exchanges.binance.orders import BinanceOrderProvider


def _algo(**overrides) -> dict:
    raw = {
        "algoId": 1000000232433997,
        "clientAlgoId": "tp-0e6b31c5ad5347de",
        "algoType": "CONDITIONAL",
        "orderType": "TAKE_PROFIT_MARKET",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "positionSide": "BOTH",
        "timeInForce": "GTC",
        "quantity": "0.1198",
        "algoStatus": "FINISHED",
        "actualOrderId": "28622351169",
        "actualPrice": "83187.700000",
        "actualQty": "0.1198",
        "triggerPrice": "83180.00",
        "price": "0.00",
        "reduceOnly": True,
        "closePosition": False,
        "createTime": 1791393000000,
        "updateTime": 1791394133164,
        "triggerTime": 1791394133047,
    }
    raw.update(overrides)
    return raw


def test_finished_maps_to_filled_with_actual_execution():
    order = map_algo_order(_algo())
    assert order.exchange_order_id == "1000000232433997"
    assert order.client_order_id == "tp-0e6b31c5ad5347de"
    assert order.type is OrderType.TAKE_PROFIT_MARKET
    assert order.status is OrderStatus.FILLED
    assert order.executed_quantity == Decimal("0.1198")
    assert order.average_price == Decimal("83187.7")
    assert order.stop_price == Decimal(83180)
    assert order.price is None
    assert order.market_type is MarketType.FUTURES


@pytest.mark.parametrize(
    ("algo_status", "expected"),
    [
        ("NEW", OrderStatus.NEW),
        ("TRIGGERING", OrderStatus.NEW),
        ("CANCELED", OrderStatus.CANCELED),
        ("EXPIRED", OrderStatus.EXPIRED),
    ],
)
def test_other_statuses_without_execution(algo_status, expected):
    order = map_algo_order(
        _algo(algoStatus=algo_status, actualOrderId="", actualPrice="0.000000", actualQty=None)
    )
    assert order.status is expected
    assert order.executed_quantity == 0
    assert order.average_price is None


def test_unknown_status_raises():
    with pytest.raises(ValueError, match="algoStatus"):
        map_algo_order(_algo(algoStatus="SOMETHING_NEW"))


async def test_get_conditional_order_queries_algo_endpoint():
    client = MagicMock()
    client.request = AsyncMock(return_value=_algo())
    provider = BinanceOrderProvider(client)

    order = await provider.get_conditional_order("BTCUSDT", "1000000232433997")

    assert order.status is OrderStatus.FILLED
    args, kwargs = client.request.call_args
    assert args == ("GET", "/fapi/v1/algoOrder")
    assert kwargs["params"] == {"symbol": "BTCUSDT", "algoId": "1000000232433997"}
    assert kwargs["signed"] is True
