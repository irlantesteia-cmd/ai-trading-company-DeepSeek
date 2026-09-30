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
from app.domain.models.signal import Signal
from app.orchestration.orchestrator import Orchestrator


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


def _build_orchestrator(context) -> Orchestrator:
    registry = context.registry
    registry.register(RiskAgent(context))
    registry.register(PortfolioAgent(context))
    registry.register(ExecutionAgent(context))
    registry.register(TradingManager(context))
    return Orchestrator(context=context, registry=registry)


@pytest.mark.asyncio
async def test_start_stop(context):
    orch = _build_orchestrator(context)
    await orch.start()
    assert orch.running is True
    await orch.stop()
    assert orch.running is False


@pytest.mark.asyncio
async def test_submit_signal_rejected_by_risk(context):
    orch = _build_orchestrator(context)
    await orch.start()

    order = await orch.submit_signal(_signal(confidence=0.3))
    assert order is None
    context.exchange.orders.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_submit_signal_full_pipeline(context):
    orch = _build_orchestrator(context)
    await orch.start()

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

    order = await orch.submit_signal(_signal(confidence=0.9))
    assert order is fake_order
    context.exchange.orders.place_order.assert_awaited_once()

    sent_request = context.exchange.orders.place_order.await_args.args[0]
    assert sent_request.symbol == "BTCUSDT"
    assert sent_request.client_order_id.startswith("ai-")