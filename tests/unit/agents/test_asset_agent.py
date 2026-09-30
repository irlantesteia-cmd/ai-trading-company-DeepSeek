import pytest

from app.agents.asset_agent import AssetAgent
from app.core.enums import MarketType, SignalDirection
from app.domain.models.signal import Signal
from app.events.event import CandleClosed, SignalGenerated


class _ConstantAgent(AssetAgent):
    """AssetAgent que sempre retorna um sinal LONG."""

    async def analyze(self) -> Signal | None:
        return self._build_signal(
            direction=SignalDirection.LONG,
            confidence=0.8,
            rationale="test",
        )


def _agent(context) -> AssetAgent:
    return AssetAgent(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
    )


def test_name_derives_from_symbol(context):
    a = _agent(context)
    assert a.name == "asset::BTCUSDT"
    assert a.symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_default_analyze_returns_none(context):
    a = _agent(context)
    assert await a.analyze() is None


@pytest.mark.asyncio
async def test_tick_publishes_signal_generated(context):
    a = _ConstantAgent(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    received = []

    async def handler(event: SignalGenerated):
        received.append(event)

    context.event_bus.subscribe(SignalGenerated, handler)
    signal = await a.tick()
    assert signal is not None
    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].direction == "LONG"


@pytest.mark.asyncio
async def test_subscription_filters_symbol_and_interval(context):
    a = _ConstantAgent(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    await a.start()

    # Evento de outro símbolo → ignorado
    await context.event_bus.publish(
        CandleClosed(symbol="ETHUSDT", interval="5m", close=3000.0)
    )
    # Evento do intervalo errado → ignorado
    await context.event_bus.publish(
        CandleClosed(symbol="BTCUSDT", interval="1h", close=60000.0)
    )
    # Evento correto → processa
    await context.event_bus.publish(
        CandleClosed(symbol="BTCUSDT", interval="5m", close=60000.0)
    )
    assert a.started


@pytest.mark.asyncio
async def test_build_signal_uses_agent_name(context):
    a = _ConstantAgent(
        context,
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        interval="15m",
    )
    s = await a.analyze()
    assert s is not None
    assert s.agent == "asset::SOLUSDT"
    assert s.symbol == "SOLUSDT"
    assert s.horizon == "15m"