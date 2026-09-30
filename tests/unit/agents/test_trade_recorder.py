from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.agents.trade_recorder import TradeRecorderAgent
from app.core.enums import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from app.domain.models.order import Order, OrderFill
from app.events.event import OrderFilled


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _order_with_two_fills() -> Order:
    return Order(
        exchange_order_id="1",
        client_order_id="c-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.FILLED,
        quantity=Decimal(1),
        executed_quantity=Decimal(1),
        average_price=Decimal(60005),
        position_side=PositionSide.LONG,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        fills=[
            OrderFill(
                price=Decimal(60000),
                quantity=Decimal("0.5"),
                commission=Decimal("0.01"),
                commission_asset="USDT",
                timestamp=datetime.now(UTC),
            ),
            OrderFill(
                price=Decimal(60010),
                quantity=Decimal("0.5"),
                commission=Decimal("0.01"),
                commission_asset="USDT",
                timestamp=datetime.now(UTC),
            ),
        ],
    )


def _attach_session(context, session) -> None:
    context.session_factory = lambda: session


@pytest.mark.asyncio
async def test_persists_one_row_per_fill(context):
    session = _FakeSession()
    _attach_session(context, session)
    agent = TradeRecorderAgent(context)
    await agent.start()

    order = _order_with_two_fills()
    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=1.0,
            average_price=60005.0,
            order=order,
        )
    )

    assert session.committed is True
    assert len(session.added) == 2
    t0, t1 = session.added
    assert t0.trade_id == "1-0"
    assert t0.order_id == "1"
    assert t0.symbol == "BTCUSDT"
    assert t0.market_type == "FUTURES"
    assert t0.side == "BUY"
    assert t0.position_side == "LONG"
    assert t0.quantity == Decimal("0.5")
    assert t0.price == Decimal(60000)
    assert t0.fee == Decimal("0.01")
    assert t0.fee_asset == "USDT"
    assert t1.trade_id == "1-1"
    assert t1.price == Decimal(60010)


@pytest.mark.asyncio
async def test_skips_when_order_payload_missing(context):
    session = _FakeSession()
    _attach_session(context, session)
    agent = TradeRecorderAgent(context)
    await agent.start()

    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id="1",
            symbol="BTCUSDT",
            filled_quantity=1.0,
            average_price=60000.0,
            order=None,
        )
    )
    assert session.committed is False
    assert session.added == []


@pytest.mark.asyncio
async def test_skips_when_session_factory_missing(context):
    context.session_factory = None
    agent = TradeRecorderAgent(context)
    await agent.start()

    order = _order_with_two_fills()
    # Não deve levantar — apenas loga e ignora
    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=1.0,
            average_price=60005.0,
            order=order,
        )
    )


@pytest.mark.asyncio
async def test_skips_when_no_fills(context):
    session = _FakeSession()
    _attach_session(context, session)
    agent = TradeRecorderAgent(context)
    await agent.start()

    order = _order_with_two_fills().model_copy(update={"fills": []})
    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=1.0,
            average_price=60005.0,
            order=order,
        )
    )
    assert session.committed is False
    assert session.added == []


@pytest.mark.asyncio
async def test_handles_integrity_error_as_duplicate(context):
    """Duplicata (trade_id já existe) → rollback + log, sem explodir."""

    class _FailingSession(_FakeSession):
        async def commit(self) -> None:
            raise IntegrityError("duplicate key", None, Exception("orig"))

    session = _FailingSession()
    _attach_session(context, session)
    agent = TradeRecorderAgent(context)
    await agent.start()

    order = _order_with_two_fills()
    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=1.0,
            average_price=60005.0,
            order=order,
        )
    )
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_spot_order_without_position_side(context):
    session = _FakeSession()
    _attach_session(context, session)
    agent = TradeRecorderAgent(context)
    await agent.start()

    order = Order(
        exchange_order_id="2",
        client_order_id="c-2",
        symbol="ETHUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        status=OrderStatus.FILLED,
        quantity=Decimal(2),
        executed_quantity=Decimal(2),
        average_price=Decimal(3000),
        position_side=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        fills=[
            OrderFill(
                price=Decimal(3000),
                quantity=Decimal(2),
                commission=Decimal("0.5"),
                commission_asset="USDT",
                timestamp=datetime.now(UTC),
            ),
        ],
    )
    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            filled_quantity=2.0,
            average_price=3000.0,
            order=order,
        )
    )
    assert len(session.added) == 1
    assert session.added[0].position_side is None
    assert session.added[0].market_type == "SPOT"