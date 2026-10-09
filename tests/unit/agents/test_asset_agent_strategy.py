from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.agents.asset_agent import AssetAgent
from app.core.enums import MarketType, SignalDirection
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.events.event import SignalGenerated
from app.strategies.base import Strategy
from tests.unit.strategies.conftest import make_candles


class _AlwaysLong(Strategy):
    name = "always_long"

    @property
    def warmup(self) -> int:
        return 3

    def generate(self, ctx: StrategyContext) -> Signal | None:
        entry = ctx.candles[-1].close
        return Signal(
            signal_id=str(uuid4()),
            symbol=ctx.symbol,
            market_type=ctx.market_type,
            direction=SignalDirection.LONG,
            confidence=0.9,
            horizon=ctx.interval,
            suggested_entry=entry,
            suggested_stop=entry - Decimal(1),
            suggested_target=entry + Decimal(2),
            strategy=self.name,
            agent="test",
            generated_at=datetime.now(UTC),
        )


class _AgentWithCandles(AssetAgent):
    def __init__(self, *args, candles, **kwargs):
        super().__init__(*args, **kwargs)
        self._candles = candles

    async def _fetch_candles(self):
        return self._candles


@pytest.mark.asyncio
async def test_agent_returns_none_without_strategy(context):
    agent = AssetAgent(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    assert await agent.analyze() is None


@pytest.mark.asyncio
async def test_agent_uses_strategy(context):
    candles = make_candles([100.0] * 5)
    agent = _AgentWithCandles(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        strategy=_AlwaysLong(),
        candles=candles,
    )
    sig = await agent.analyze()
    assert sig is not None
    assert sig.direction == SignalDirection.LONG


@pytest.mark.asyncio
async def test_agent_returns_none_when_warmup_not_met(context):
    candles = make_candles([100.0] * 2)  # warmup = 3
    agent = _AgentWithCandles(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        strategy=_AlwaysLong(),
        candles=candles,
    )
    assert await agent.analyze() is None


@pytest.mark.asyncio
async def test_tick_publishes_signal_from_strategy(context):
    candles = make_candles([100.0] * 5)
    agent = _AgentWithCandles(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        strategy=_AlwaysLong(),
        candles=candles,
    )
    received = []

    async def handler(event: SignalGenerated):
        received.append(event)

    context.event_bus.subscribe(SignalGenerated, handler)

    sig = await agent.tick()
    assert sig is not None
    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].direction == "LONG"

@pytest.mark.asyncio
async def test_agent_ignores_in_progress_candle(context):
    from unittest.mock import AsyncMock

    closed = make_candles([100.0, 101.0, 102.0, 103.0])
    in_progress = closed[-1].model_copy(
        update={
            "open_time": closed[-1].close_time,
            "close_time": closed[-1].close_time + (closed[-1].close_time - closed[-1].open_time),
            "close": Decimal(999),
            "closed": False,
        }
    )
    context.exchange.market_data.get_candles = AsyncMock(return_value=[*closed, in_progress])
    agent = AssetAgent(
        context,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        strategy=_AlwaysLong(),
    )

    signal = await agent.analyze()

    assert signal is not None
    assert signal.suggested_entry == closed[-1].close
