import pytest

from app.events.bus import EventBus
from app.events.event import OrderFilled, TickerUpdated


@pytest.mark.asyncio
async def test_publish_dispatches_to_subscriber():
    bus = EventBus()
    received = []

    async def handler(event: TickerUpdated):
        received.append(event)

    bus.subscribe(TickerUpdated, handler)
    await bus.publish(
        TickerUpdated(symbol="BTCUSDT", market_type="SPOT", last=60000.0)
    )

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_handler_isolated_on_failure():
    bus = EventBus()
    received = []

    async def boom(_):
        raise RuntimeError("fail")

    async def ok(event: TickerUpdated):
        received.append(event)

    bus.subscribe(TickerUpdated, boom)
    bus.subscribe(TickerUpdated, ok)

    await bus.publish(
        TickerUpdated(symbol="ETHUSDT", market_type="SPOT", last=3000.0)
    )
    assert len(received) == 1


@pytest.mark.asyncio
async def test_events_are_type_scoped():
    bus = EventBus()
    got_ticker = []
    got_fill = []

    async def h_ticker(e: TickerUpdated):
        got_ticker.append(e)

    async def h_fill(e: OrderFilled):
        got_fill.append(e)

    bus.subscribe(TickerUpdated, h_ticker)
    bus.subscribe(OrderFilled, h_fill)

    await bus.publish(
        OrderFilled(
            exchange_order_id="1",
            symbol="BTCUSDT",
            filled_quantity=1.0,
            average_price=60000.0,
        )
    )
    assert not got_ticker
    assert len(got_fill) == 1