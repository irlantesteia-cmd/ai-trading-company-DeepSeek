from decimal import Decimal

import pytest

from app.core.enums import MarketType, OrderSide, OrderType
from app.domain.models.order import OrderRequest
from app.exchanges.base.orders import OrderProvider


class _MinimalProvider(OrderProvider):
    async def place_order(self, request):
        raise AssertionError

    async def cancel_order(self, symbol, exchange_order_id, market_type):
        raise AssertionError

    async def get_order(self, symbol, exchange_order_id, market_type):
        raise AssertionError

    async def list_open_orders(self, symbol, market_type):
        return []


async def test_conditional_orders_are_opt_in():
    provider = _MinimalProvider()
    request = OrderRequest(
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.SELL,
        type=OrderType.STOP_MARKET,
        quantity=Decimal(1),
        stop_price=Decimal(90),
        client_order_id="sl-test",
    )
    with pytest.raises(NotImplementedError, match="_MinimalProvider"):
        await provider.place_conditional_order(request)
    with pytest.raises(NotImplementedError):
        await provider.cancel_all_algo_orders("BTCUSDT")
