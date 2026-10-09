from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType, OrderSide, OrderStatus, OrderType
from app.domain.models.order import Order
from app.runtime.recovery import Reconciler


class _Row:
    def __init__(
        self, exchange_order_id: str, *, is_conditional: bool = False, symbol: str = "BTCUSDT"
    ) -> None:
        self.exchange_order_id = exchange_order_id
        self.is_conditional = is_conditional
        self.symbol = symbol


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


def _exchange() -> MagicMock:
    exchange = MagicMock()
    exchange.orders.list_open_conditional_orders = AsyncMock(return_value=[])
    return exchange


def _factory(rows):
    def _make():
        return _FakeSession(rows)

    return _make


@pytest.mark.asyncio
async def test_balanced_when_sets_match():
    rows = [_Row("1"), _Row("2")]
    exchange = _exchange()
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
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[_order("1")])

    r = Reconciler(exchange=exchange, session_factory=_factory(rows))
    report = await r.reconcile()
    assert report.balanced is False
    assert report.missing_on_exchange == ["2"]
    assert report.missing_in_db == []


@pytest.mark.asyncio
async def test_missing_in_db():
    rows = [_Row("1")]
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(
        return_value=[_order("1"), _order("2")]
    )

    r = Reconciler(exchange=exchange, session_factory=_factory(rows))
    report = await r.reconcile()
    assert report.balanced is False
    assert report.missing_in_db == ["2"]


@pytest.mark.asyncio
async def test_report_as_dict_keys():
    exchange = _exchange()
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

# ------------------------------------------------- sincronização (fase 3b)
class _CapturingFactory:
    """Session factory fake que também guarda o SELECT executado."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.statements: list = []

    def __call__(self):
        outer = self

        class _S(_FakeSession):
            async def execute(self, stmt):
                outer.statements.append(stmt)
                return _FakeResult(outer.rows)

        return _S(self.rows)


class _Recorder:
    def __init__(self) -> None:
        self.records: list[tuple[Order, bool]] = []

    async def record(self, order: Order, *, is_conditional: bool = False) -> None:
        self.records.append((order, is_conditional))


def _final(exchange_id: str, status: OrderStatus) -> Order:
    return _order(exchange_id).model_copy(update={"status": status})


@pytest.mark.asyncio
async def test_syncs_final_status_of_regular_and_conditional_orders():
    rows = [_Row("1"), _Row("777", is_conditional=True, symbol="SOLUSDT")]
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    exchange.orders.get_order = AsyncMock(return_value=_final("1", OrderStatus.CANCELED))
    exchange.orders.get_conditional_order = AsyncMock(
        return_value=_final("777", OrderStatus.FILLED)
    )
    recorder = _Recorder()

    r = Reconciler(exchange=exchange, session_factory=_factory(rows), recorder=recorder)
    report = await r.reconcile()

    assert report.missing_on_exchange == ["1", "algo:777"]
    assert report.synced == ["1", "algo:777"]
    assert report.unresolved == []
    assert report.balanced is True
    exchange.orders.get_conditional_order.assert_awaited_once_with("SOLUSDT", "777")
    assert [(o.status, cond) for o, cond in recorder.records] == [
        (OrderStatus.CANCELED, False),
        (OrderStatus.FILLED, True),
    ]


@pytest.mark.asyncio
async def test_adopts_exchange_orders_missing_in_db():
    algo = _order("888")
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    exchange.orders.list_open_conditional_orders = AsyncMock(return_value=[algo])
    recorder = _Recorder()

    r = Reconciler(exchange=exchange, session_factory=_factory([]), recorder=recorder)
    report = await r.reconcile()

    assert report.missing_in_db == ["algo:888"]
    assert report.synced == ["algo:888"]
    assert report.balanced is True
    assert recorder.records == [(algo, True)]


@pytest.mark.asyncio
async def test_fetch_failure_stays_unresolved():
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    exchange.orders.get_order = AsyncMock(side_effect=RuntimeError("timeout"))
    recorder = _Recorder()

    r = Reconciler(exchange=exchange, session_factory=_factory([_Row("1")]), recorder=recorder)
    report = await r.reconcile()

    assert report.unresolved == ["1"]
    assert report.balanced is False
    assert recorder.records == []


@pytest.mark.asyncio
async def test_still_open_after_fetch_is_not_a_divergence():
    """O listing de algo orders devolve [] em erro: a consulta direta desfaz o alarme."""
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    exchange.orders.get_conditional_order = AsyncMock(
        return_value=_final("777", OrderStatus.NEW)
    )
    r = Reconciler(
        exchange=exchange,
        session_factory=_factory([_Row("777", is_conditional=True)]),
        recorder=_Recorder(),
    )
    report = await r.reconcile()
    assert report.balanced is True
    assert report.synced == ["algo:777"]


@pytest.mark.asyncio
async def test_without_conditional_support_they_are_left_out_of_db_query():
    from sqlalchemy.dialects import postgresql

    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    exchange.orders.list_open_conditional_orders = AsyncMock(side_effect=NotImplementedError)
    factory = _CapturingFactory([])

    await Reconciler(exchange=exchange, session_factory=factory).reconcile()

    sql = str(factory.statements[0].compile(dialect=postgresql.dialect()))
    assert "orders.is_conditional IS false" in sql


@pytest.mark.asyncio
async def test_spot_does_not_query_conditional_orders():
    exchange = _exchange()
    exchange.orders.list_open_orders = AsyncMock(return_value=[])
    await Reconciler(exchange=exchange, session_factory=_factory([])).reconcile(
        market_type=MarketType.SPOT
    )
    exchange.orders.list_open_conditional_orders.assert_not_awaited()
