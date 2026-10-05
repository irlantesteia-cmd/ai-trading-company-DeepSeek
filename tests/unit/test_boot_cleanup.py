"""Testes para `close_all_futures_positions_on_boot`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.core.enums import MarginType, PositionSide
from app.domain.models.position import FuturesPosition
from app.runtime.boot_cleanup import close_all_futures_positions_on_boot


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