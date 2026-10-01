from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.events.bus import EventBus
from app.events.event import CandleClosed
from app.market.candle_stream import CandleStreamService

T0 = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
NOW = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def _candle(*, open_time: datetime) -> Candle:
    """Cria um candle com close_time = open_time + 5min."""
    return Candle(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=5),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
        trades=10,
        closed=True,
    )


def _service(candles: list[Candle]) -> tuple[CandleStreamService, EventBus, MagicMock]:
    exchange = MagicMock()
    exchange.market_data.get_candles = AsyncMock(return_value=candles)
    bus = EventBus()
    svc = CandleStreamService(
        exchange=exchange,
        event_bus=bus,
        symbol="BTCUSDT",
        interval="5m",
        market_type=MarketType.FUTURES,
        poll_interval_s=0.01,
    )
    return svc, bus, exchange


async def _collect(bus: EventBus) -> list[CandleClosed]:
    received: list[CandleClosed] = []

    async def handler(event: CandleClosed) -> None:
        received.append(event)

    bus.subscribe(CandleClosed, handler)
    return received


@pytest.mark.asyncio
async def test_emits_closed_candle():
    svc, bus, _ = _service([_candle(open_time=T0)])
    received = await _collect(bus)

    emitted = await svc.poll_once(now=NOW)

    assert emitted is not None
    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].interval == "5m"
    assert received[0].close == 100.0
    assert svc.last_emitted_open_time == T0


@pytest.mark.asyncio
async def test_skips_open_candle():
    future = NOW + timedelta(minutes=10)
    svc, bus, _ = _service([_candle(open_time=future)])
    received = await _collect(bus)

    emitted = await svc.poll_once(now=NOW)

    assert emitted is None
    assert received == []
    assert svc.last_emitted_open_time is None


@pytest.mark.asyncio
async def test_does_not_emit_same_candle_twice():
    svc, bus, _ = _service([_candle(open_time=T0)])
    received = await _collect(bus)

    await svc.poll_once(now=NOW)
    await svc.poll_once(now=NOW)
    await svc.poll_once(now=NOW)

    assert len(received) == 1


@pytest.mark.asyncio
async def test_emits_new_candle_after_previous():
    t1 = T0 + timedelta(minutes=5)

    svc, bus, exchange = _service([_candle(open_time=T0)])
    received = await _collect(bus)

    await svc.poll_once(now=NOW)
    assert len(received) == 1

    exchange.market_data.get_candles = AsyncMock(
        return_value=[_candle(open_time=t1)]
    )
    await svc.poll_once(now=NOW)
    assert len(received) == 2
    assert svc.last_emitted_open_time == t1


@pytest.mark.asyncio
async def test_picks_latest_closed_when_multiple():
    """get_candles pode devolver várias barras; emitimos só a mais recente fechada."""
    older = _candle(open_time=T0)
    newer = _candle(open_time=T0 + timedelta(minutes=5))
    svc, bus, _ = _service([older, newer])
    received = await _collect(bus)

    await svc.poll_once(now=NOW)
    assert len(received) == 1
    assert svc.last_emitted_open_time == newer.open_time


@pytest.mark.asyncio
async def test_empty_candles_no_emission():
    svc, bus, _ = _service([])
    received = await _collect(bus)

    assert await svc.poll_once(now=NOW) is None
    assert received == []


@pytest.mark.asyncio
async def test_all_open_candles_no_emission():
    future = NOW + timedelta(minutes=10)
    svc, bus, _ = _service([_candle(open_time=future)])
    received = await _collect(bus)

    assert await svc.poll_once(now=NOW) is None
    assert received == []


@pytest.mark.asyncio
async def test_poll_once_propagates_exchange_errors():
    exchange = MagicMock()
    exchange.market_data.get_candles = AsyncMock(side_effect=RuntimeError("boom"))
    bus = EventBus()
    svc = CandleStreamService(
        exchange=exchange,
        event_bus=bus,
        symbol="BTCUSDT",
        interval="5m",
        market_type=MarketType.FUTURES,
        poll_interval_s=0.01,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await svc.poll_once(now=NOW)


def test_invalid_poll_interval():
    exchange = MagicMock()
    bus = EventBus()
    with pytest.raises(ValueError):
        CandleStreamService(
            exchange=exchange,
            event_bus=bus,
            symbol="BTCUSDT",
            interval="5m",
            market_type=MarketType.FUTURES,
            poll_interval_s=0,
        )


def test_exposes_symbol_and_interval():
    svc, _, _ = _service([])
    assert svc.symbol == "BTCUSDT"
    assert svc.interval == "5m"