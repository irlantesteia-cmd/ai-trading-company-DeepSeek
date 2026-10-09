"""Testes para cancelamento de ordens condicionais (Algo Order API)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ExchangeError
from app.exchanges.binance.orders import BinanceOrderProvider


def _make_provider() -> tuple[BinanceOrderProvider, MagicMock]:
    client = MagicMock()
    client.request = AsyncMock()
    return BinanceOrderProvider(client), client


def _algo_payload(algo_id: int, symbol: str = "SOLUSDT", type_: str = "STOP_MARKET") -> dict:
    return {
        "algoId": algo_id,
        "clientAlgoId": f"sl-{algo_id}",
        "algoType": "CONDITIONAL",
        "orderType": type_,
        "symbol": symbol,
        "side": "SELL",
        "positionSide": "BOTH",
        "quantity": "83.37",
        "algoStatus": "NEW",
        "triggerPrice": "119.50",
        "price": "0.0000",
        "workingType": "MARK_PRICE",
        "closePosition": True,
        "reduceOnly": True,
        "createTime": int(datetime.now(UTC).timestamp() * 1000),
        "updateTime": int(datetime.now(UTC).timestamp() * 1000),
    }


async def test_list_open_algo_orders_parses_list_response() -> None:
    provider, client = _make_provider()
    client.request.return_value = [
        _algo_payload(1, "SOLUSDT", "STOP_MARKET"),
        _algo_payload(2, "SOLUSDT", "TAKE_PROFIT_MARKET"),
    ]

    orders = await provider.list_open_algo_orders("SOLUSDT")

    assert len(orders) == 2
    assert orders[0].exchange_order_id == "1"
    assert orders[0].type.value == "STOP_MARKET"
    assert orders[1].type.value == "TAKE_PROFIT_MARKET"


async def test_list_open_algo_orders_parses_dict_wrapper() -> None:
    provider, client = _make_provider()
    client.request.return_value = {"orders": [_algo_payload(42, "XRPUSDT")]}

    orders = await provider.list_open_algo_orders("XRPUSDT")

    assert len(orders) == 1
    assert orders[0].exchange_order_id == "42"


async def test_list_open_algo_orders_returns_empty_on_error() -> None:
    provider, client = _make_provider()
    client.request.side_effect = ExchangeError("404 endpoint")

    orders = await provider.list_open_algo_orders("SOLUSDT")

    assert orders == []


async def test_cancel_all_algo_orders_cancels_each() -> None:
    provider, client = _make_provider()
    listing = [_algo_payload(1), _algo_payload(2)]
    # Primeira chamada: listing; seguintes: DELETE por ordem
    client.request.side_effect = [listing, {}, {}]

    canceled = await provider.cancel_all_algo_orders("SOLUSDT")

    assert canceled == 2
    # 1 GET + 2 DELETE
    assert client.request.await_count == 3


async def test_cancel_all_algo_orders_isolates_failure() -> None:
    provider, client = _make_provider()
    listing = [_algo_payload(1), _algo_payload(2)]
    # GET ok; DELETE 1 falha; DELETE 2 ok
    client.request.side_effect = [listing, ExchangeError("boom"), {}]

    canceled = await provider.cancel_all_algo_orders("SOLUSDT")

    assert canceled == 1


async def test_cancel_all_algo_orders_returns_zero_when_empty() -> None:
    provider, client = _make_provider()
    client.request.return_value = []

    canceled = await provider.cancel_all_algo_orders("SOLUSDT")

    assert canceled == 0
    # Só a chamada de GET
    assert client.request.await_count == 1


@pytest.fixture(autouse=True)
def _disable_side_effects() -> None:
    """Placeholder para futuras fixtures; evita import unused."""
    return