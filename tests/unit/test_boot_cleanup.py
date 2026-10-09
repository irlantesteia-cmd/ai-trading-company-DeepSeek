"""Testes para `close_all_futures_positions_on_boot`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.core.config import settings
from app.core.enums import (
    MarginType,
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from app.domain.models.order import Order
from app.domain.models.position import FuturesPosition
from app.runtime.boot_cleanup import (
    cancel_orphan_conditional_orders_on_boot,
    close_all_futures_positions_on_boot,
)


def _make_position(symbol: str, side: PositionSide, qty: str) -> FuturesPosition:
    return FuturesPosition(
        symbol=symbol,
        position_side=side,
        quantity=Decimal(qty),
        entry_price=Decimal(100),
        mark_price=Decimal(100),
        unrealized_pnl=Decimal(0),
        realized_pnl=Decimal(0),
        leverage=1,
        margin_type=MarginType.CROSSED,
        isolated_margin=Decimal(0),
        liquidation_price=None,
        updated_at=datetime.now(UTC),
    )


def _make_exchange(
    positions: list[FuturesPosition],
    *,
    list_exc: Exception | None = None,
    close_side_effect: list | None = None,
) -> MagicMock:
    exchange = MagicMock()
    if list_exc is not None:
        exchange.account.get_futures_positions = AsyncMock(side_effect=list_exc)
    else:
        exchange.account.get_futures_positions = AsyncMock(return_value=positions)
    if close_side_effect is not None:
        exchange.positions.close_position = AsyncMock(side_effect=close_side_effect)
    else:
        exchange.positions.close_position = AsyncMock(return_value=None)
    return exchange


async def test_no_positions_returns_zero() -> None:
    exchange = _make_exchange([])

    closed = await close_all_futures_positions_on_boot(exchange)

    assert closed == 0
    exchange.positions.close_position.assert_not_called()


async def test_closes_all_open_positions() -> None:
    positions = [
        _make_position("SOLUSDT", PositionSide.LONG, "82.13"),
        _make_position("XRPUSDT", PositionSide.SHORT, "6490.1"),
    ]
    exchange = _make_exchange(positions)

    closed = await close_all_futures_positions_on_boot(exchange)

    assert closed == 2
    assert exchange.positions.close_position.await_count == 2
    exchange.positions.close_position.assert_any_await("SOLUSDT", PositionSide.LONG)
    exchange.positions.close_position.assert_any_await("XRPUSDT", PositionSide.SHORT)


async def test_one_failure_does_not_block_others() -> None:
    positions = [
        _make_position("SOLUSDT", PositionSide.LONG, "82.13"),
        _make_position("XRPUSDT", PositionSide.SHORT, "6490.1"),
    ]
    exchange = _make_exchange(
        positions,
        close_side_effect=[RuntimeError("boom"), None],
    )

    closed = await close_all_futures_positions_on_boot(exchange)

    assert closed == 1
    assert exchange.positions.close_position.await_count == 2


async def test_list_failure_returns_zero_without_raising() -> None:
    exchange = _make_exchange([], list_exc=RuntimeError("network down"))

    closed = await close_all_futures_positions_on_boot(exchange)

    assert closed == 0
    exchange.positions.close_position.assert_not_called()

# ---------------------------------------------- ordens condicionais órfãs

def _algo(symbol: str) -> Order:
    now = datetime.now(UTC)
    return Order(
        exchange_order_id=f"algo-{symbol}",
        client_order_id=f"tp-{symbol}",
        symbol=symbol,
        market_type=MarketType.FUTURES,
        side=OrderSide.SELL,
        type=OrderType.TAKE_PROFIT_MARKET,
        status=OrderStatus.NEW,
        quantity=Decimal(1),
        executed_quantity=Decimal(0),
        created_at=now,
        updated_at=now,
    )


def _orphan_exchange(positions, open_conditional, *, cancel_side_effect=None) -> MagicMock:
    exchange = _make_exchange(positions)
    exchange.orders.list_open_conditional_orders = AsyncMock(return_value=open_conditional)
    exchange.orders.cancel_all_algo_orders = AsyncMock(
        side_effect=cancel_side_effect, return_value=1
    )
    return exchange


async def test_cancels_only_symbols_without_position() -> None:
    exchange = _orphan_exchange(
        [_make_position("SOLUSDT", PositionSide.LONG, "1")],
        [_algo("SOLUSDT"), _algo("XRPUSDT"), _algo("BTCUSDT"), _algo("XRPUSDT")],
    )

    canceled = await cancel_orphan_conditional_orders_on_boot(exchange)

    assert canceled == 2
    calls = [c.args[0] for c in exchange.orders.cancel_all_algo_orders.await_args_list]
    assert calls == ["BTCUSDT", "XRPUSDT"]  # SOLUSDT tem posição: preservado


async def test_cancel_failure_is_isolated_per_symbol() -> None:
    exchange = _orphan_exchange(
        [], [_algo("BTCUSDT"), _algo("XRPUSDT")],
        cancel_side_effect=[RuntimeError("timeout"), 3],
    )

    canceled = await cancel_orphan_conditional_orders_on_boot(exchange)

    assert canceled == 3
    assert exchange.orders.cancel_all_algo_orders.await_count == 2


async def test_nothing_canceled_when_positions_cannot_be_listed() -> None:
    exchange = _orphan_exchange([], [_algo("BTCUSDT")])
    exchange.account.get_futures_positions = AsyncMock(side_effect=RuntimeError("auth"))

    assert await cancel_orphan_conditional_orders_on_boot(exchange) == 0
    exchange.orders.cancel_all_algo_orders.assert_not_awaited()


async def test_disabled_by_setting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cancel_protective_orders_on_close", False)
    exchange = _orphan_exchange([], [_algo("BTCUSDT")])

    assert await cancel_orphan_conditional_orders_on_boot(exchange) == 0
    exchange.orders.list_open_conditional_orders.assert_not_awaited()


async def test_provider_without_conditional_support_is_skipped() -> None:
    exchange = _orphan_exchange([], [])
    exchange.orders.list_open_conditional_orders = AsyncMock(side_effect=NotImplementedError)

    assert await cancel_orphan_conditional_orders_on_boot(exchange) == 0
    exchange.orders.cancel_all_algo_orders.assert_not_awaited()


async def test_no_orphans_is_a_noop() -> None:
    exchange = _orphan_exchange(
        [_make_position("BTCUSDT", PositionSide.LONG, "1")], [_algo("BTCUSDT")]
    )

    assert await cancel_orphan_conditional_orders_on_boot(exchange) == 0
    exchange.orders.cancel_all_algo_orders.assert_not_awaited()
