import pytest

from app.agents.base import BaseAgent
from app.core.enums import AgentRole
from app.events.event import Event, TickerUpdated


class _Dummy(BaseAgent):
    role = AgentRole.QA
    name = "dummy"

    def __init__(self, context, *, subscribe: bool = True) -> None:
        super().__init__(context)
        self._subscribe = subscribe
        self.received: list[Event] = []
        self.started_called = False
        self.stopped_called = False

    def subscriptions(self):
        if not self._subscribe:
            return {}
        return {TickerUpdated: self._on_ticker}

    async def _on_ticker(self, event: Event) -> None:
        self.received.append(event)

    async def on_start(self) -> None:
        self.started_called = True

    async def on_stop(self) -> None:
        self.stopped_called = True


@pytest.mark.asyncio
async def test_start_subscribes_and_fires_hook(context):
    a = _Dummy(context)
    await a.start()
    assert a.started
    assert a.started_called

    await context.event_bus.publish(
        TickerUpdated(symbol="BTCUSDT", market_type="SPOT", last=1.0)
    )
    assert len(a.received) == 1


@pytest.mark.asyncio
async def test_stop_unsubscribes(context):
    a = _Dummy(context)
    await a.start()
    await a.stop()
    assert not a.started
    assert a.stopped_called

    await context.event_bus.publish(
        TickerUpdated(symbol="BTCUSDT", market_type="SPOT", last=1.0)
    )
    assert a.received == []


@pytest.mark.asyncio
async def test_start_is_idempotent(context):
    a = _Dummy(context)
    await a.start()
    await a.start()  # não deve rodar de novo
    assert a.started


@pytest.mark.asyncio
async def test_agent_without_subscriptions(context):
    a = _Dummy(context, subscribe=False)
    await a.start()
    await a.stop()
    assert not a.started