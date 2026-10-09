"""Testes do RoundTripAgent — helpers puros e fluxo de classificação."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.round_trip import RoundTripAgent
from app.core.enums import (
    CloseReason,
    MarketType,
    OrderSide,
    OrderType,
    PositionSide,
    TradeRole,
)
from app.database.models.round_trip import RoundTripORM
from app.domain.models.order import Order, OrderFill


def _fill(qty: str, price: str, fee: str = "0") -> OrderFill:
    return OrderFill(
        price=Decimal(price),
        quantity=Decimal(qty),
        commission=Decimal(fee),
        commission_asset="USDT",
        timestamp=datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC),
        trade_id="1",
    )


def _order(
    *,
    side: OrderSide = OrderSide.BUY,
    client_order_id: str = "ai-abc",
) -> Order:
    return Order(
        exchange_order_id="X-1",
        client_order_id=client_order_id,
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        side=side,
        type=OrderType.MARKET,
        status="FILLED",
        quantity=Decimal(100),
        executed_quantity=Decimal(100),
        average_price=Decimal(100),
        created_at=datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC),
        fills=[],
    )


@pytest.fixture
def context() -> MagicMock:
    ctx = MagicMock()
    ctx.session_factory = None
    ctx.event_bus.publish = AsyncMock()
    ctx.exchange.orders.cancel_all_algo_orders = AsyncMock(return_value=2)
    return ctx


@pytest.fixture
def agent(context: MagicMock) -> RoundTripAgent:
    return RoundTripAgent(context)


# ------------------------------------------------------------- helpers
def test_is_same_side_long_buy(agent: RoundTripAgent) -> None:
    assert agent._is_same_side(PositionSide.LONG.value, "BUY") is True
    assert agent._is_same_side(PositionSide.LONG.value, "SELL") is False


def test_is_same_side_short_sell(agent: RoundTripAgent) -> None:
    assert agent._is_same_side(PositionSide.SHORT.value, "SELL") is True
    assert agent._is_same_side(PositionSide.SHORT.value, "BUY") is False


def test_reason_stop_loss(agent: RoundTripAgent) -> None:
    assert agent._reason_from_client_order_id("sl-abc") is CloseReason.STOP_LOSS


def test_reason_take_profit(agent: RoundTripAgent) -> None:
    assert agent._reason_from_client_order_id("tp-abc") is CloseReason.TAKE_PROFIT


def test_reason_manual(agent: RoundTripAgent) -> None:
    assert agent._reason_from_client_order_id("close-abc") is CloseReason.MANUAL


def test_reason_unknown(agent: RoundTripAgent) -> None:
    assert agent._reason_from_client_order_id("ai-abc") is CloseReason.UNKNOWN


# ------------------------------------------------------------- lifecycle
def test_new_trip_from_buy_order(agent: RoundTripAgent) -> None:
    trip = agent._new_trip(
        _order(side=OrderSide.BUY),
        [_fill("100", "50", fee="0.5")],
    )
    assert trip.position_side == PositionSide.LONG.value
    assert trip.entry_quantity == Decimal(100)
    assert trip.entry_avg_price == Decimal(50)
    assert trip.entry_fee == Decimal("0.5")
    assert trip.exit_quantity == Decimal(0)
    assert trip.status == "OPEN"


def test_new_trip_from_sell_order(agent: RoundTripAgent) -> None:
    trip = agent._new_trip(
        _order(side=OrderSide.SELL),
        [_fill("10", "100")],
    )
    assert trip.position_side == PositionSide.SHORT.value


def test_scale_in_updates_weighted_average(agent: RoundTripAgent) -> None:
    trip = RoundTripORM(
        round_trip_id="rt-1",
        symbol="SOLUSDT",
        market_type="FUTURES",
        position_side="LONG",
        status="OPEN",
        entry_quantity=Decimal(100),
        entry_avg_price=Decimal(50),
        entry_fee=Decimal(0),
        exit_quantity=Decimal(0),
        exit_fee=Decimal(0),
        opened_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    agent._apply_scale_in(trip, [_fill("100", "60", fee="1")])
    assert trip.entry_quantity == Decimal(200)
    assert trip.entry_avg_price == Decimal(55)
    assert trip.entry_fee == Decimal(1)


def test_apply_exit_accumulates(agent: RoundTripAgent) -> None:
    trip = RoundTripORM(
        round_trip_id="rt-1",
        symbol="SOLUSDT",
        market_type="FUTURES",
        position_side="LONG",
        status="OPEN",
        entry_quantity=Decimal(100),
        entry_avg_price=Decimal(50),
        entry_fee=Decimal(0),
        exit_quantity=Decimal(0),
        exit_fee=Decimal(0),
        opened_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    agent._apply_exit(trip, [_fill("40", "55", fee="0.2")])
    agent._apply_exit(trip, [_fill("60", "56", fee="0.3")])
    assert trip.exit_quantity == Decimal(100)
    assert trip.exit_fee == Decimal("0.5")
    assert trip.exit_avg_price == Decimal("55.6")


def test_close_trip_long_profit(agent: RoundTripAgent) -> None:
    trip = RoundTripORM(
        round_trip_id="rt-1",
        symbol="SOLUSDT",
        market_type="FUTURES",
        position_side="LONG",
        status="OPEN",
        entry_quantity=Decimal(100),
        entry_avg_price=Decimal(50),
        entry_fee=Decimal("0.5"),
        exit_quantity=Decimal(100),
        exit_avg_price=Decimal(55),
        exit_fee=Decimal("0.6"),
        opened_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    order = _order(side=OrderSide.SELL, client_order_id="tp-abc")
    agent._close_trip(trip, order)
    assert trip.status == "CLOSED"
    assert trip.close_reason == CloseReason.TAKE_PROFIT.value
    assert trip.gross_pnl == Decimal(500)
    assert trip.net_pnl == Decimal("498.9")


def test_close_trip_short_profit(agent: RoundTripAgent) -> None:
    trip = RoundTripORM(
        round_trip_id="rt-1",
        symbol="SOLUSDT",
        market_type="FUTURES",
        position_side="SHORT",
        status="OPEN",
        entry_quantity=Decimal(100),
        entry_avg_price=Decimal(60),
        entry_fee=Decimal(0),
        exit_quantity=Decimal(100),
        exit_avg_price=Decimal(50),
        exit_fee=Decimal(0),
        opened_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    agent._close_trip(trip, _order(side=OrderSide.BUY, client_order_id="tp-abc"))
    assert trip.gross_pnl == Decimal(1000)
    assert trip.net_pnl == Decimal(1000)


def test_close_trip_short_loss(agent: RoundTripAgent) -> None:
    trip = RoundTripORM(
        round_trip_id="rt-1",
        symbol="SOLUSDT",
        market_type="FUTURES",
        position_side="SHORT",
        status="OPEN",
        entry_quantity=Decimal(100),
        entry_avg_price=Decimal(50),
        entry_fee=Decimal(0),
        exit_quantity=Decimal(100),
        exit_avg_price=Decimal(55),
        exit_fee=Decimal(0),
        opened_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    agent._close_trip(trip, _order(side=OrderSide.BUY, client_order_id="sl-abc"))
    assert trip.gross_pnl == Decimal(-500)
    assert trip.close_reason == CloseReason.STOP_LOSS.value


def test_is_fully_closed_with_tolerance(agent: RoundTripAgent) -> None:
    trip = RoundTripORM(
        round_trip_id="rt-1",
        symbol="SOLUSDT",
        market_type="FUTURES",
        position_side="LONG",
        status="OPEN",
        entry_quantity=Decimal(100),
        entry_avg_price=Decimal(50),
        entry_fee=Decimal(0),
        exit_quantity=Decimal("99.99999999"),
        exit_fee=Decimal(0),
        opened_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    assert agent._is_fully_closed(trip) is True


# ------------------------------------------------------------- siblings
def test_should_cancel_siblings_true_on_sl(agent: RoundTripAgent) -> None:
    order = _order(side=OrderSide.BUY, client_order_id="sl-abc")
    assert agent._should_cancel_siblings(order, TradeRole.EXIT.value) is True


def test_should_cancel_siblings_true_on_tp(agent: RoundTripAgent) -> None:
    order = _order(side=OrderSide.SELL, client_order_id="tp-abc")
    assert agent._should_cancel_siblings(order, TradeRole.EXIT.value) is True


def test_should_cancel_siblings_false_on_manual_close(agent: RoundTripAgent) -> None:
    order = _order(side=OrderSide.BUY, client_order_id="close-abc")
    assert agent._should_cancel_siblings(order, TradeRole.EXIT.value) is False


def test_should_cancel_siblings_false_on_entry(agent: RoundTripAgent) -> None:
    order = _order(side=OrderSide.BUY, client_order_id="sl-abc")
    assert agent._should_cancel_siblings(order, TradeRole.ENTRY.value) is False


async def test_maybe_cancel_siblings_calls_exchange(
    agent: RoundTripAgent, context: MagicMock
) -> None:
    order = _order(side=OrderSide.SELL, client_order_id="tp-abc")

    await agent._maybe_cancel_siblings(order, TradeRole.EXIT.value)

    context.exchange.orders.cancel_all_algo_orders.assert_awaited_once_with(
        "SOLUSDT"
    )


async def test_maybe_cancel_siblings_noop_on_entry(
    agent: RoundTripAgent, context: MagicMock
) -> None:
    order = _order(side=OrderSide.BUY, client_order_id="sl-abc")

    await agent._maybe_cancel_siblings(order, TradeRole.ENTRY.value)

    context.exchange.orders.cancel_all_algo_orders.assert_not_awaited()


async def test_maybe_cancel_siblings_isolates_failure(
    agent: RoundTripAgent, context: MagicMock
) -> None:
    context.exchange.orders.cancel_all_algo_orders.side_effect = RuntimeError("boom")
    order = _order(side=OrderSide.BUY, client_order_id="sl-abc")

    # Não deve propagar
    await agent._maybe_cancel_siblings(order, TradeRole.EXIT.value)

    context.exchange.orders.cancel_all_algo_orders.assert_awaited_once()