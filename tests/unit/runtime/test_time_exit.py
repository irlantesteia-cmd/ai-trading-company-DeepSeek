from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import CloseReason, MarketType, OrderSide, OrderStatus, OrderType, PositionSide
from app.domain.models.order import Order, OrderFill
from app.events.event import OrderFilled
from app.runtime.time_exit import TimeExitMonitor

NOW = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)


def _trip(symbol: str, side: str, minutes_ago: float, trip_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol, position_side=side, round_trip_id=trip_id,
        opened_at=NOW - timedelta(minutes=minutes_ago),
    )


class _Session:
    def __init__(self, rows) -> None:
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        rows = self._rows
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        return result


def _close_order(symbol: str = "XRPUSDT") -> Order:
    return Order(
        exchange_order_id="555", client_order_id="tx-abc", symbol=symbol,
        market_type=MarketType.FUTURES, side=OrderSide.SELL, type=OrderType.MARKET,
        status=OrderStatus.FILLED, quantity=Decimal(10), executed_quantity=Decimal(10),
        average_price=Decimal("1.3884"), created_at=NOW, updated_at=NOW,
        fills=[OrderFill(price=Decimal("1.3884"), quantity=Decimal(10), commission=Decimal("0.01"),
                         commission_asset="USDT", timestamp=NOW, trade_id="1")],
    )


def _monitor(rows, *, close_side_effect=None, close_return=None):
    exchange = MagicMock()
    exchange.positions.close_position = AsyncMock(
        side_effect=close_side_effect, return_value=close_return or _close_order()
    )
    bus = MagicMock()
    bus.publish = AsyncMock()
    # Rows em ordem decrescente de opened_at, como a query devolve.
    rows = sorted(rows, key=lambda r: r.opened_at, reverse=True)
    monitor = TimeExitMonitor(
        exchange=exchange, event_bus=bus, session_factory=lambda: _Session(rows),
        max_holding_minutes=30,
    )
    return monitor, exchange, bus


async def test_closes_only_positions_held_past_the_limit():
    monitor, exchange, bus = _monitor(
        [_trip("XRPUSDT", "LONG", 31, "old"), _trip("SOLUSDT", "SHORT", 10, "young")]
    )

    assert await monitor.check_once(now=NOW) == 1

    exchange.positions.close_position.assert_awaited_once_with(
        "XRPUSDT", PositionSide.LONG, client_order_prefix="tx"
    )
    (event,) = [c.args[0] for c in bus.publish.await_args_list]
    assert isinstance(event, OrderFilled) and event.exchange_order_id == "555"


async def test_uses_newest_open_trip_per_symbol_and_side():
    """Um OPEN antigo esquecido não pode fechar uma posição nova."""
    monitor, exchange, _ = _monitor(
        [_trip("XRPUSDT", "LONG", 300, "stale"), _trip("XRPUSDT", "LONG", 5, "current")]
    )

    assert await monitor.check_once(now=NOW) == 0
    exchange.positions.close_position.assert_not_awaited()


async def test_no_position_on_exchange_is_not_counted():
    monitor, _, bus = _monitor([_trip("XRPUSDT", "LONG", 45, "t")])
    monitor._exchange.positions.close_position = AsyncMock(return_value=None)

    assert await monitor.check_once(now=NOW) == 0
    bus.publish.assert_not_awaited()


async def test_close_failure_is_isolated_per_position():
    monitor, exchange, _ = _monitor(
        [_trip("XRPUSDT", "LONG", 45, "a"), _trip("BTCUSDT", "SHORT", 40, "b")],
        close_side_effect=[RuntimeError("timeout"), _close_order("BTCUSDT")],
    )

    assert await monitor.check_once(now=NOW) == 1
    assert exchange.positions.close_position.await_count == 2


def test_limit_must_be_positive():
    with pytest.raises(ValueError):
        TimeExitMonitor(exchange=MagicMock(), event_bus=MagicMock(), session_factory=MagicMock(),
                        max_holding_minutes=0)


@pytest.mark.parametrize(
    ("client_order_id", "reason"),
    [
        ("sl-1", CloseReason.STOP_LOSS),
        ("tp-1", CloseReason.TAKE_PROFIT),
        ("tx-1", CloseReason.TIME_EXIT),
        ("close-1", CloseReason.MANUAL),
        ("ai-1", CloseReason.UNKNOWN),
    ],
)
def test_close_reason_from_client_order_id(client_order_id, reason):
    assert CloseReason.from_client_order_id(client_order_id) is reason
