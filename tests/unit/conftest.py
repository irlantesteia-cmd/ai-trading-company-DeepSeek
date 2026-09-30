from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.domain.models.position import SpotBalance
from app.events.bus import EventBus
from app.orchestration.context import AgentContext
from app.orchestration.registry import AgentRegistry


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def fake_exchange() -> MagicMock:
    from decimal import Decimal

    exchange = MagicMock()
    exchange.orders.place_order = AsyncMock()
    exchange.account.get_futures_positions = AsyncMock(return_value=[])
    exchange.account.get_futures_balance = AsyncMock(
        return_value=SpotBalance(
            asset="USDT", free=Decimal(10000), locked=Decimal(0)
        )
    )
    return exchange


@pytest.fixture
def context(event_bus: EventBus, fake_exchange: MagicMock) -> AgentContext:
    registry = AgentRegistry()
    return AgentContext(
        exchange=fake_exchange,
        event_bus=event_bus,
        settings=settings,
        registry=registry,
    )