"""R3: falhas operacionais (reconciliação, API, proteção) criam trava."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.trading_manager import TradingManager
from app.core.config import settings
from app.core.enums import (
    HaltScope,
    HaltSource,
    MarginType,
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SignalDirection,
)
from app.core.exceptions import ExchangeAuthError, ExchangeError, ExchangeOrderRejectedError
from app.domain.models.order import Order
from app.domain.models.position import FuturesPosition
from app.domain.models.signal import Signal
from app.events.event import HealthCheckFailed, SignalGenerated
from app.exchanges.binance.orders import BinanceOrderProvider
from app.runtime.halt_guard import ConsecutiveFailures, HaltGuard
from app.runtime.reconcile_monitor import ReconcileMonitor
from app.runtime.recovery import ReconciliationReport

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- HaltGuard
class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self) -> None:
        pass


@pytest.fixture
def repo(monkeypatch):
    calls: list[dict] = []
    state = {"created": object()}

    async def create_if_absent(_self, **kw):
        calls.append(kw)
        if isinstance(state["created"], Exception):
            raise state["created"]
        return state["created"]

    monkeypatch.setattr(
        "app.database.repositories.trading_halt.TradingHaltRepository.create_if_absent",
        create_if_absent,
    )
    return calls, state


def _guard() -> tuple[HaltGuard, AsyncMock]:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return HaltGuard(session_factory=_Session, event_bus=bus, created_by="tester"), bus.publish


async def test_trip_creates_auto_halt_and_alerts_once(repo):
    calls, _ = repo
    guard, publish = _guard()
    assert await guard.trip(rule="api_errors", reason="3 erros")
    (call,) = calls
    assert call["scope"] is HaltScope.GLOBAL and call["symbol"] is None
    assert call["source"] is HaltSource.AUTO and call["created_by"] == "tester"
    (event,) = [c.args[0] for c in publish.await_args_list]
    assert isinstance(event, HealthCheckFailed) and event.component == "trading_halt"
    assert event.detail == "TRADING HALTED (api_errors): 3 erros"


async def test_trip_with_symbol_is_symbol_scoped(repo):
    calls, _ = repo
    guard, publish = _guard()
    await guard.trip(rule="protection", reason="sl falhou", symbol="SOLUSDT")
    assert calls[0]["scope"] is HaltScope.SYMBOL and calls[0]["symbol"] == "SOLUSDT"
    assert "(protection SOLUSDT)" in publish.await_args.args[0].detail


async def test_trip_existing_or_failing_does_not_alert(repo, caplog):
    _, state = repo
    guard, publish = _guard()
    state["created"] = None  # já havia trava ativa
    assert not await guard.trip(rule="api_errors", reason="x")
    state["created"] = RuntimeError("db down")
    assert not await guard.trip(rule="api_errors", reason="x")
    publish.assert_not_awaited()
    assert "halt_guard.trip_failed" in caplog.text


async def test_trip_without_database_only_logs():
    bus = MagicMock()
    bus.publish = AsyncMock()
    guard = HaltGuard(session_factory=None, event_bus=bus, created_by="t")
    assert not await guard.trip(rule="api_errors", reason="x")
    bus.publish.assert_not_awaited()


def test_consecutive_failures():
    c = ConsecutiveFailures(3)
    assert [c.failure(), c.failure()] == [False, False]
    c.success()
    assert [c.failure(), c.failure(), c.failure(), c.failure()] == [False, False, True, True]
    off = ConsecutiveFailures(0)
    assert not any(off.failure() for _ in range(10))


# ------------------------------------------------------- TradingManager
def _event() -> SignalGenerated:
    signal = Signal(
        signal_id="s1", symbol="BTCUSDT", market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG, confidence=0.9, horizon="5m",
        strategy="momentum", agent="asset::BTCUSDT", generated_at=NOW,
    )
    return SignalGenerated(signal_id="s1", symbol="BTCUSDT", agent="a", direction="LONG",
                           confidence=0.9, signal=signal)


@pytest.fixture
def manager(context, monkeypatch):
    monkeypatch.setattr(settings, "signal_auto_execution_enabled", True)
    monkeypatch.setattr(settings, "halt_api_errors", 3)
    trips: list[dict] = []

    async def trip(_self, **kw):
        trips.append(kw)
        return True

    monkeypatch.setattr("app.runtime.halt_guard.HaltGuard.trip", trip)
    return TradingManager(context), trips


async def test_api_errors_halt_only_after_consecutive_limit(manager):
    tm, trips = manager
    tm.process_signal = AsyncMock(side_effect=ExchangeOrderRejectedError("-2019 margin"))
    await tm._on_signal_generated(_event())
    await tm._on_signal_generated(_event())
    assert trips == []
    await tm._on_signal_generated(_event())
    (trip,) = trips
    assert trip["rule"] == "api_errors" and "3 erros seguidos" in trip["reason"]


async def test_successful_order_resets_the_count(manager):
    tm, trips = manager
    tm.process_signal = AsyncMock(side_effect=[
        ExchangeError("a"), ExchangeError("b"), object(), ExchangeError("c"), ExchangeError("d"),
    ])
    for _ in range(5):
        await tm._on_signal_generated(_event())
    assert trips == []


async def test_risk_rejection_and_value_errors_do_not_count(manager):
    tm, trips = manager
    tm.process_signal = AsyncMock(side_effect=[ExchangeError("a"), ExchangeError("b"), None,
                                               ValueError("equity 0"), ExchangeError("c")])
    for _ in range(5):
        await tm._on_signal_generated(_event())
    assert trips[0]["rule"] == "api_errors"  # rejeição de risco (None) não zera nem conta


async def test_auth_error_halts_immediately(manager):
    tm, trips = manager
    tm.process_signal = AsyncMock(side_effect=ExchangeAuthError("-2015 invalid key"))
    await tm._on_signal_generated(_event())
    (trip,) = trips
    assert trip["rule"] == "api_auth"


# ------------------------------------------------------ ReconcileMonitor
def _position(symbol: str, qty: str = "1") -> FuturesPosition:
    return FuturesPosition(
        symbol=symbol, position_side=PositionSide.BOTH, quantity=Decimal(qty),
        entry_price=Decimal(100), mark_price=Decimal(100), unrealized_pnl=Decimal(0),
        realized_pnl=Decimal(0), leverage=1, margin_type=MarginType.CROSSED,
        isolated_margin=Decimal(0), updated_at=NOW,
    )


def _algo(symbol: str, type_: OrderType = OrderType.STOP_MARKET) -> Order:
    return Order(
        exchange_order_id=f"algo-{symbol}", client_order_id=f"sl-{symbol}", symbol=symbol,
        market_type=MarketType.FUTURES, side=OrderSide.SELL, type=type_, status=OrderStatus.NEW,
        quantity=Decimal(1), executed_quantity=Decimal(0), created_at=NOW, updated_at=NOW,
    )


def _monitor(reports, *, positions=(), stops=(), **overrides):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(side_effect=list(reports))
    exchange = MagicMock()
    exchange.account.get_futures_positions = AsyncMock(return_value=list(positions))
    exchange.orders.list_open_conditional_orders = AsyncMock(return_value=list(stops))
    guard = MagicMock()
    guard.trip = AsyncMock(return_value=True)
    bus = MagicMock()
    bus.publish = AsyncMock()
    cfg = settings.model_copy(update={
        "stop_loss_enabled": True, "halt_reconciliation_runs": 2,
        "halt_reconciliation_errors": 3, "halt_unprotected_runs": 2, **overrides,
    })
    monitor = ReconcileMonitor(reconciler=reconciler, exchange=exchange, guard=guard,
                               event_bus=bus, settings=cfg)
    return monitor, guard.trip, exchange


def _rules(trip: AsyncMock) -> list[str]:
    return [c.kwargs["rule"] for c in trip.await_args_list]


OK = ReconciliationReport()
BAD = ReconciliationReport(unresolved=["123"], balanced=False)


async def test_divergence_halts_on_second_consecutive_run():
    monitor, trip, _ = _monitor([BAD, OK, BAD, BAD])
    for _ in range(3):
        await monitor.run_once()
    assert _rules(trip) == []  # BAD, OK (zera), BAD
    await monitor.run_once()
    assert _rules(trip) == ["reconciliation"]


async def test_failing_runs_halt_after_limit_and_auth_halts_at_once():
    monitor, trip, _ = _monitor([RuntimeError("x"), RuntimeError("x"), OK,
                                 RuntimeError("x"), RuntimeError("x"), RuntimeError("x")])
    for _ in range(5):
        await monitor.run_once()
    assert _rules(trip) == []
    await monitor.run_once()
    assert _rules(trip) == ["reconciliation_error"]

    auth, auth_trip, _ = _monitor([ExchangeAuthError("-2015")])
    await auth.run_once()
    assert _rules(auth_trip) == ["api_auth"]


async def test_position_without_stop_halts_on_second_check():
    positions = [_position("BTCUSDT"), _position("ETHUSDT"), _position("SOLUSDT", "0")]
    stops = [_algo("BTCUSDT"), _algo("ETHUSDT", OrderType.TAKE_PROFIT_MARKET)]  # ETH só com TP
    monitor, trip, exchange = _monitor([OK, OK], positions=positions, stops=stops)
    await monitor.run_once()
    assert _rules(trip) == []
    await monitor.run_once()
    (call,) = trip.await_args_list
    assert call.kwargs["rule"] == "unprotected_position" and "ETHUSDT" in call.kwargs["reason"]
    assert "BTCUSDT" not in call.kwargs["reason"]
    exchange.orders.list_open_conditional_orders.assert_awaited_with(None, strict=True)


async def test_protected_again_resets_the_symbol_count():
    monitor, trip, exchange = _monitor([OK, OK, OK], positions=[_position("ETHUSDT")])
    await monitor.run_once()
    exchange.orders.list_open_conditional_orders.return_value = [_algo("ETHUSDT")]
    await monitor.run_once()
    exchange.orders.list_open_conditional_orders.return_value = []
    await monitor.run_once()
    assert _rules(trip) == []


async def test_protection_check_off_without_stop_loss():
    monitor, trip, exchange = _monitor([OK, OK], positions=[_position("ETHUSDT")],
                                       stop_loss_enabled=False)
    await monitor.run_once()
    await monitor.run_once()
    exchange.account.get_futures_positions.assert_not_awaited()
    assert _rules(trip) == []


async def test_listing_failure_is_an_error_not_an_unprotected_position():
    monitor, trip, exchange = _monitor([OK, OK, OK], positions=[_position("ETHUSDT")])
    exchange.orders.list_open_conditional_orders.side_effect = ExchangeError("timeout")
    for _ in range(3):
        await monitor.run_once()
    assert _rules(trip) == ["reconciliation_error"]


# ------------------------------------------------- listagem estrita (Binance)
async def test_strict_algo_listing_raises_instead_of_empty():
    client = MagicMock()
    client.request = AsyncMock(side_effect=ExchangeError("timeout"))
    provider = BinanceOrderProvider(client)
    assert await provider.list_open_conditional_orders(None) == []
    with pytest.raises(ExchangeError):
        await provider.list_open_conditional_orders(None, strict=True)
