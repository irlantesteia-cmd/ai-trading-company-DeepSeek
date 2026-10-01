from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents.execution import ExecutionAgent
from app.agents.portfolio import PortfolioAgent
from app.agents.risk import RiskAgent
from app.agents.trading_manager import TradingManager
from app.core.enums import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    SignalDirection,
)
from app.domain.models.order import Order
from app.domain.models.position import SpotBalance
from app.domain.models.signal import Signal
from app.events.event import SignalApproved, SignalGenerated, SignalRejected


def _signal(confidence: float = 0.9) -> Signal:
    return Signal(
        signal_id="s-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        confidence=confidence,
        horizon="5m",
        suggested_entry=Decimal(60000),
        suggested_stop=Decimal(59000),
        strategy="momentum",
        agent="asset::BTCUSDT",
        generated_at=datetime.now(UTC),
    )


def _wire(context) -> TradingManager:
    registry = context.registry
    registry.register(RiskAgent(context))
    registry.register(PortfolioAgent(context))
    registry.register(ExecutionAgent(context))
    manager = TradingManager(context)
    registry.register(manager)
    return manager


@pytest.mark.asyncio
async def test_rejects_low_confidence_signal(context):
    manager = _wire(context)
    rejected: list = []

    async def handler(event: SignalRejected):
        rejected.append(event)

    context.event_bus.subscribe(SignalRejected, handler)

    order = await manager.process_signal(_signal(confidence=0.2))
    assert order is None
    assert len(rejected) == 1
    context.exchange.orders.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_approves_and_places_order(context):
    manager = _wire(context)
    approved: list = []

    async def handler(event: SignalApproved):
        approved.append(event)

    context.event_bus.subscribe(SignalApproved, handler)

    fake_order = Order(
        exchange_order_id="X-1",
        client_order_id="c-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.NEW,
        quantity=Decimal("0.1"),
        executed_quantity=Decimal(0),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    context.exchange.orders.place_order.return_value = fake_order

    order = await manager.process_signal(_signal(confidence=0.9))
    assert order is fake_order
    assert len(approved) == 1

    sent_request = context.exchange.orders.place_order.await_args.args[0]
    assert sent_request.symbol == "BTCUSDT"
    assert sent_request.side == OrderSide.BUY


@pytest.mark.asyncio
async def test_auto_execution_disabled_ignores_signal(context):
    context.settings.signal_auto_execution_enabled = False
    manager = _wire(context)
    await manager.start()

    await context.event_bus.publish(
        SignalGenerated(
            signal_id="s-1",
            symbol="BTCUSDT",
            agent="asset::BTCUSDT",
            direction="LONG",
            confidence=0.9,
            signal=_signal(),
        )
    )

    context.exchange.orders.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_auto_execution_enabled_processes_signal(context):
    context.settings.signal_auto_execution_enabled = True
    manager = _wire(context)

    fake_order = Order(
        exchange_order_id="X-1",
        client_order_id="c-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.NEW,
        quantity=Decimal("0.1"),
        executed_quantity=Decimal(0),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    context.exchange.orders.place_order.return_value = fake_order
    await manager.start()

    await context.event_bus.publish(
        SignalGenerated(
            signal_id="s-1",
            symbol="BTCUSDT",
            agent="asset::BTCUSDT",
            direction="LONG",
            confidence=0.9,
            signal=_signal(),
        )
    )

    context.exchange.orders.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_execution_enabled_missing_payload_is_ignored(context):
    context.settings.signal_auto_execution_enabled = True
    manager = _wire(context)
    await manager.start()

    await context.event_bus.publish(
        SignalGenerated(
            signal_id="s-1",
            symbol="BTCUSDT",
            agent="asset::BTCUSDT",
            direction="LONG",
            confidence=0.9,
            signal=None,
        )
    )

    context.exchange.orders.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_value_error_in_sizing_does_not_crash_and_does_not_place(context):
    """Equity zero → ValueError no sizer → warning, sem ordem, sem crash."""
    context.settings.signal_auto_execution_enabled = True
    manager = _wire(context)

    context.exchange.account.get_futures_balance.return_value = SpotBalance(
        asset="USDT", free=Decimal(0), locked=Decimal(0)
    )
    await manager.start()

    await context.event_bus.publish(
        SignalGenerated(
            signal_id="s-1",
            symbol="BTCUSDT",
            agent="asset::BTCUSDT",
            direction="LONG",
            confidence=0.9,
            signal=_signal(),
        )
    )

    context.exchange.orders.place_order.assert_not_called()