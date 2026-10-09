from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.agents.execution import ExecutionAgent
from app.core.enums import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.domain.models.asset import TradingPair
from app.domain.models.order import Order, OrderFill
from app.domain.models.order_intent import OrderIntent
from app.exchanges.symbol_info import SymbolInfoService


def _pair(symbol: str, *, step: str, price_precision: int = 2) -> TradingPair:
    return TradingPair(
        symbol=symbol,
        base=symbol[:-4],
        quote="USDT",
        market_type=MarketType.FUTURES,
        price_precision=price_precision,
        quantity_precision=2,
        min_notional=Decimal(5),
        min_quantity=Decimal("0.01"),
        step_size=Decimal(step),
        status="TRADING",
    )


def _intent(symbol: str, qty: str, *, stop: str | None = None) -> OrderIntent:
    return OrderIntent(
        intent_id=str(uuid4()),
        signal_id="s-1",
        symbol=symbol,
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        quantity=Decimal(qty),
        order_type=OrderType.MARKET,
        stop_price=Decimal(stop) if stop else None,
    )


def _order(
    *,
    symbol: str = "SOLUSDT",
    status: OrderStatus = OrderStatus.NEW,
    fills: list[OrderFill] | None = None,
    average_price: Decimal | None = None,
    executed_quantity: Decimal = Decimal(0),
) -> Order:
    return Order(
        exchange_order_id="X-1",
        client_order_id="c-1",
        symbol=symbol,
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=status,
        quantity=Decimal("85.23"),
        executed_quantity=executed_quantity,
        average_price=average_price,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        fills=fills or [],
    )


@pytest.mark.asyncio
async def test_execution_quantizes_quantity(context):
    si = SymbolInfoService()
    si.put(_pair("SOLUSDT", step="0.01"))
    agent = ExecutionAgent(context, symbol_info=si)

    context.exchange.orders.place_order.return_value = _order()
    context.exchange.orders.get_order.return_value = _order()

    await agent.execute(_intent("SOLUSDT", "85.23695874"))

    sent = context.exchange.orders.place_order.await_args.args[0]
    assert sent.quantity == Decimal("85.23")


@pytest.mark.asyncio
async def test_execution_quantizes_stop_price(context):
    si = SymbolInfoService()
    si.put(_pair("SOLUSDT", step="0.01", price_precision=2))
    agent = ExecutionAgent(context, symbol_info=si)

    context.exchange.orders.place_order.return_value = _order()
    context.exchange.orders.get_order.return_value = _order()

    await agent.execute(
        _intent("SOLUSDT", "85.23695874", stop="117.76018254")
    )

    sent = context.exchange.orders.place_order.await_args.args[0]
    assert sent.stop_price == Decimal("117.76")


@pytest.mark.asyncio
async def test_execution_fails_when_quantized_qty_zero(context):
    si = SymbolInfoService()
    si.put(_pair("XRPUSDT", step="1"))
    agent = ExecutionAgent(context, symbol_info=si)

    with pytest.raises(ValueError, match="quantização"):
        await agent.execute(_intent("XRPUSDT", "0.5"))


@pytest.mark.asyncio
async def test_execution_polls_for_fill_until_filled(context):
    """place_order devolve NEW sem fills; get_order devolve FILLED."""
    si = SymbolInfoService()
    si.put(_pair("SOLUSDT", step="0.01"))
    agent = ExecutionAgent(context, symbol_info=si)

    pending = _order(status=OrderStatus.NEW)
    filled = _order(
        status=OrderStatus.FILLED,
        executed_quantity=Decimal("85.23"),
        average_price=Decimal("118.5"),
        fills=[
            OrderFill(
                price=Decimal("118.5"),
                quantity=Decimal("85.23"),
                commission=Decimal("0.05"),
                commission_asset="USDT",
                timestamp=datetime.now(UTC),
            )
        ],
    )
    context.exchange.orders.place_order.return_value = pending
    context.exchange.orders.get_order.return_value = filled

    order = await agent.execute(_intent("SOLUSDT", "85.23"))

    assert order.status is OrderStatus.FILLED
    assert len(order.fills) == 1
    context.exchange.orders.get_order.assert_awaited()