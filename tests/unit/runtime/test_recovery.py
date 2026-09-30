from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType, OrderSide, OrderStatus, OrderType
from app.domain.models.order import Order
from app.runtime.recovery import Reconciler


class _Row:
    def __init__(self, exchange_order_id: str) -> None:
        self.exchange_order_id = exchange_order_id


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


def _order(exchange_id: str) -> Order:
    return Order(
        exchange_order_id=exchange_id,
        client_order_id=f"c-{exchange_id}",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal(1),
        executed_quantity=Decimal(0),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _factory(rows):
    def _make():
        return _FakeSession(rows)

    return _make


@pytest.mark.asyncio
async def test_balanced_when_sets_match():
    rows = [_Row("1"), _Row("2")]
    exchange = MagicMock()
    exchange.orders.list_open_orders = AsyncMock(
        return_value=[_order("1"), _order("2")]
    )

    r = Reconciler(exchange=exchange, session_factory=_factory(rows))
    report = await r.reconcile(symbol="BTCUSDT")
    assert report.balanced is True
    assert report.missing_on_exchange == []
    assert report.missing_in_db == []


@pytest.mark.asyncio
async def test_missing_on_exchange():
    rows = [_Row("1"), _Row("2")]
    exchange = MagicMock()
    exchange.orders.list_open_orders = AsyncMock(return_value=[_order("1")])

    r = Reconciler(exchange=exchange, session_factory=_factory(rows))
    report = await r.reconcile()
    assert report.balanced is False
    assert report.missing_on_exchange == ["2"]
    assert report.missing_in_db == []


@pytest.mark.asyncio
async def test_missing_in_db():
    rows = [_Row("1")]
    exchange = MagicMock()
    exchange.orders.list_open_orders = AsyncMock(
        return_value=[_order("1"), _order("2")]
    )

    r = Reconciler(exchange=exchange, session_factory=_factory(rows))
    report = await r.reconcile()
    assert report.balanced is False
    assert report.missing_in_db == ["2"]


@pytest.mark.asyncio
async def test_report_as_dict_keys():
    exchange = MagicMock()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])

    r = Reconciler(exchange=exchange, session_factory=_factory([]))
    report = await r.reconcile()
    d = report.as_dict()
    assert set(d.keys()) >= {
        "checked_at",
        "db_open_orders",
        "exchange_open_orders",
        "missing_on_exchange",
        "missing_in_db",
        "balanced",
    }