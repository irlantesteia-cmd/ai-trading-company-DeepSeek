from decimal import Decimal
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
    exchange = MagicMock()
    exchange.orders.place_order = AsyncMock()
    exchange.orders.get_order = AsyncMock()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    exchange.orders.cancel_order = AsyncMock()
    exchange.account.get_futures_positions = AsyncMock(return_value=[])
    exchange.account.get_futures_balance = AsyncMock(
        return_value=SpotBalance(
            asset="USDT",
            free=Decimal(10000),
            locked=Decimal(0),
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


@pytest.fixture(autouse=True)
def _disable_protective_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desliga SL/TP durante TODOS os testes unitários.

    Protege contra `.env` de desenvolvimento com `STOP_LOSS_ENABLED=true`
    quebrando asserções que esperam apenas a ordem de entrada.
    """
    monkeypatch.setattr(settings, "stop_loss_enabled", False)
    monkeypatch.setattr(settings, "take_profit_enabled", False)