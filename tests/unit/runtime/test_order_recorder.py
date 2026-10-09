"""Persistência de ordens: recorder, provider que grava, UDS e adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType, OrderSide, OrderStatus, OrderType
from app.core.exceptions import ExchangeError
from app.domain.models.order import Order, OrderRequest
from app.events.event import OrderFilled
from app.exchanges.binance.adapter import BinanceAdapter
from app.runtime.order_recorder import OrderRecorder, RecordingOrderProvider
from app.runtime.user_stream_handler import handle_user_stream_event
from tests.unit.test_user_stream import _trade_update_payload


def _order(status: OrderStatus = OrderStatus.FILLED, client_id: str = "ai-1") -> Order:
    now = datetime.now(UTC)
    return Order(
        exchange_order_id="999",
        client_order_id=client_id,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=status,
        quantity=Decimal(1),
        executed_quantity=Decimal(1),
        created_at=now,
        updated_at=now,
    )


def _request(client_id: str = "ai-1", type_: OrderType = OrderType.MARKET) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_id,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=type_,
        quantity=Decimal(1),
        stop_price=Decimal(90) if type_ is not OrderType.MARKET else None,
    )


class _FakeRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[Order, bool]] = []

    async def record(self, order: Order, *, is_conditional: bool = False) -> None:
        self.records.append((order, is_conditional))

    async def record_rejected(self, request: OrderRequest, *, is_conditional: bool = False):
        await OrderRecorder.record_rejected(self, request, is_conditional=is_conditional)  # type: ignore[arg-type]


def _inner() -> MagicMock:
    inner = MagicMock()
    inner.place_order = AsyncMock(return_value=_order())
    inner.place_conditional_order = AsyncMock(return_value=_order(OrderStatus.NEW, "sl-1"))
    inner.cancel_order = AsyncMock(return_value=_order(OrderStatus.CANCELED))
    inner.get_order = AsyncMock(return_value=_order(OrderStatus.PARTIALLY_FILLED))
    inner.list_open_orders = AsyncMock(return_value=[])
    inner.list_open_algo_orders = AsyncMock(return_value=["algo"])
    return inner


# ---------------------------------------------------------- RecordingOrderProvider
async def test_records_every_order_operation():
    recorder = _FakeRecorder()
    provider = RecordingOrderProvider(_inner(), recorder)  # type: ignore[arg-type]

    await provider.place_order(_request())
    await provider.place_conditional_order(_request("sl-1", OrderType.STOP_MARKET))
    await provider.cancel_order("BTCUSDT", "999", MarketType.FUTURES)
    await provider.get_order("BTCUSDT", "999", MarketType.FUTURES)

    statuses = [(o.status, cond) for o, cond in recorder.records]
    assert statuses == [
        (OrderStatus.FILLED, False),
        (OrderStatus.NEW, True),
        (OrderStatus.CANCELED, False),
        (OrderStatus.PARTIALLY_FILLED, False),
    ]


async def test_rejected_order_is_recorded_and_error_propagates():
    recorder = _FakeRecorder()
    inner = _inner()
    inner.place_order = AsyncMock(side_effect=ExchangeError("-2019 margin is insufficient"))
    provider = RecordingOrderProvider(inner, recorder)  # type: ignore[arg-type]

    with pytest.raises(ExchangeError):
        await provider.place_order(_request("ai-rej"))

    order, cond = recorder.records[0]
    assert order.status is OrderStatus.REJECTED
    assert order.client_order_id == "ai-rej"
    assert order.exchange_order_id == ""
    assert cond is False


async def test_provider_specific_methods_are_delegated():
    provider = RecordingOrderProvider(_inner(), _FakeRecorder())  # type: ignore[arg-type]
    assert await provider.list_open_algo_orders("BTCUSDT") == ["algo"]


# ------------------------------------------------------------------ OrderRecorder
async def test_recorder_never_raises_on_db_failure(caplog):
    def _broken_factory():
        raise RuntimeError("db down")

    recorder = OrderRecorder(_broken_factory)  # type: ignore[arg-type]
    await recorder.record(_order())
    assert "order_recorder.persist_failed" in caplog.text


async def test_recorder_skips_orders_without_client_id(caplog):
    factory = MagicMock()
    await OrderRecorder(factory).record(_order(client_id=""))
    factory.assert_not_called()


# ------------------------------------------------------------- UDS handler
@pytest.mark.parametrize(
    ("execution_type", "status", "last_qty", "published"),
    [
        ("NEW", "NEW", "0", False),
        ("CANCELED", "CANCELED", "0", False),
        ("EXPIRED", "EXPIRED", "0", False),
        ("TRADE", "FILLED", "1.5", True),
    ],
)
async def test_user_stream_records_every_update_and_publishes_fills(
    execution_type, status, last_qty, published
):
    recorder = _FakeRecorder()
    bus = MagicMock()
    bus.publish = AsyncMock()

    await handle_user_stream_event(
        _trade_update_payload(
            execution_type=execution_type, order_status=status, last_qty=last_qty
        ),
        market_type=MarketType.FUTURES,
        event_bus=bus,
        recorder=recorder,  # type: ignore[arg-type]
    )

    assert [o.status.value for o, _ in recorder.records] == [status]
    assert bus.publish.await_count == (1 if published else 0)
    if published:
        assert isinstance(bus.publish.await_args.args[0], OrderFilled)


async def test_user_stream_ignores_other_events():
    recorder = _FakeRecorder()
    bus = MagicMock()
    bus.publish = AsyncMock()
    await handle_user_stream_event(
        {"e": "ACCOUNT_UPDATE"}, market_type=MarketType.FUTURES, event_bus=bus,
        recorder=recorder,  # type: ignore[arg-type]
    )
    assert recorder.records == []
    bus.publish.assert_not_awaited()


# --------------------------------------------------------------------- adapter
def test_adapter_shares_wrapped_orders_with_positions():
    wrapped: list = []

    def _wrap(inner):
        provider = RecordingOrderProvider(inner, _FakeRecorder())  # type: ignore[arg-type]
        wrapped.append(provider)
        return provider

    adapter = BinanceAdapter(api_key="k", api_secret="s", testnet=True, orders_wrapper=_wrap)

    assert adapter.orders is wrapped[0]
    # close_position usa o mesmo provider, então também grava.
    assert adapter.positions._orders is wrapped[0]  # type: ignore[attr-defined]


async def test_conditional_queries_are_recorded_as_conditional():
    inner = _inner()
    inner.get_conditional_order = AsyncMock(return_value=_order(OrderStatus.FILLED, "tp-1"))
    inner.list_open_conditional_orders = AsyncMock(return_value=[])
    recorder = _FakeRecorder()
    provider = RecordingOrderProvider(inner, recorder)  # type: ignore[arg-type]

    await provider.get_conditional_order("BTCUSDT", "123")
    assert await provider.list_open_conditional_orders("BTCUSDT") == []

    assert [(o.client_order_id, cond) for o, cond in recorder.records] == [("tp-1", True)]
