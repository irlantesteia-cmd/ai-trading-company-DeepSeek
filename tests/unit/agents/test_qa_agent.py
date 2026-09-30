import pytest

from app.agents.qa import QAAgent
from app.events.event import HealthCheckFailed, OrderFilled, OrderSubmitted


@pytest.mark.asyncio
async def test_qa_flags_invalid_fill(context):
    agent = QAAgent(context)
    await agent.start()

    received = []

    async def handler(event: HealthCheckFailed):
        received.append(event)

    context.event_bus.subscribe(HealthCheckFailed, handler)

    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id="1",
            symbol="BTCUSDT",
            filled_quantity=0.0,
            average_price=100.0,
        )
    )
    assert agent.violations == 1
    assert len(received) == 1
    assert "filled_quantity" in received[0].detail


@pytest.mark.asyncio
async def test_qa_ok_on_valid_fill(context):
    agent = QAAgent(context)
    await agent.start()
    await context.event_bus.publish(
        OrderFilled(
            exchange_order_id="1",
            symbol="BTCUSDT",
            filled_quantity=1.0,
            average_price=100.0,
        )
    )
    assert agent.violations == 0


@pytest.mark.asyncio
async def test_qa_flags_empty_client_order_id(context):
    agent = QAAgent(context)
    await agent.start()
    await context.event_bus.publish(
        OrderSubmitted(client_order_id="", symbol="BTCUSDT")
    )
    assert agent.violations == 1