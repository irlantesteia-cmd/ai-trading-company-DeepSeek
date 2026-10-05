"""Testes para o wiring de STOP_MARKET/TAKE_PROFIT_MARKET após fill."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.agents.execution import ExecutionAgent
from app.core.enums import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SignalDirection,
)
from app.domain.models.order import Order, OrderFill, OrderRequest
from app.domain.models.order_intent import OrderIntent
from app.domain.models.risk import RiskAction, RiskDecision
from app.domain.models.signal import Signal
from app.exchanges.binance.mappers import (
    order_request_to_algo_params,
    order_request_to_params,
)
from app.portfolio.sizing import PositionSizer
from app.portfolio.state import PortfolioState


# ---------------------------------------------------------------- mapper (order)
def test_mapper_close_position_omits_quantity_and_reduce_only() -> None:
    params = order_request_to_params(
        symbol="SOLUSDT",
        side=OrderSide.SELL,
        type_=OrderType.STOP_MARKET,
        quantity=Decimal("82.11"),
        client_order_id="sl-abc",
        market_type=MarketType.FUTURES,
        stop_price=Decimal("121.28"),
        close_position=True,
    )
    assert params["closePosition"] == "true"
    assert "quantity" not in params
    assert "reduceOnly" not in params
    assert "positionSide" not in params
    assert params["stopPrice"] == "121.28"
    assert params["type"] == "STOP_MARKET"


def test_mapper_close_position_ignored_on_market_order() -> None:
    params = order_request_to_params(
        symbol="SOLUSDT",
        side=OrderSide.BUY,
        type_=OrderType.MARKET,
        quantity=Decimal(1),
        client_order_id="ai-abc",
        market_type=MarketType.FUTURES,
        close_position=True,
    )
    assert "closePosition" not in params
    assert params["quantity"] == "1"


def test_mapper_close_position_respects_hedge_mode_position_side() -> None:
    params = order_request_to_params(
        symbol="SOLUSDT",
        side=OrderSide.SELL,
        type_=OrderType.STOP_MARKET,
        quantity=Decimal("82.11"),
        client_order_id="sl-abc",
        market_type=MarketType.FUTURES,
        stop_price=Decimal("121.28"),
        position_side=PositionSide.LONG,
        close_position=False,
    )
    assert params["positionSide"] == "LONG"
    assert params["quantity"] == "82.11"
    assert "closePosition" not in params
    assert "reduceOnly" not in params


# ---------------------------------------------------------------- mapper (algo)
def test_algo_params_uses_trigger_price_and_client_algo_id() -> None:
    params = order_request_to_algo_params(
        symbol="SOLUSDT",
        side=OrderSide.SELL,
        type_=OrderType.STOP_MARKET,
        quantity=Decimal("82.11"),
        client_order_id="sl-abc",
        trigger_price=Decimal("121.28"),
        close_position=True,
    )
    assert params["algoType"] == "CONDITIONAL"
    assert params["triggerPrice"] == "121.28"
    assert params["clientAlgoId"] == "sl-abc"
    assert "stopPrice" not in params
    assert "newClientOrderId" not in params
    assert params["closePosition"] == "true"
    assert "quantity" not in params
    assert "reduceOnly" not in params
    assert "positionSide" not in params


def test_algo_params_hedge_mode_uses_position_side() -> None:
    params = order_request_to_algo_params(
        symbol="SOLUSDT",
        side=OrderSide.SELL,
        type_=OrderType.TAKE_PROFIT_MARKET,
        quantity=Decimal("82.11"),
        client_order_id="tp-abc",
        trigger_price=Decimal("130.00"),
        position_side=PositionSide.LONG,
        close_position=False,
    )
    assert params["positionSide"] == "LONG"
    assert params["quantity"] == "82.11"
    assert "closePosition" not in params


def test_algo_params_rejects_market_type() -> None:
    import pytest

    from app.exchanges.binance.mappers import _ALGO_TYPES

    assert OrderType.MARKET not in _ALGO_TYPES
    with pytest.raises(ValueError):
        order_request_to_algo_params(
            symbol="SOLUSDT",
            side=OrderSide.SELL,
            type_=OrderType.MARKET,
            quantity=Decimal(1),
            client_order_id="x",
            trigger_price=Decimal(100),
        )


# ---------------------------------------------------------------- sizer
def _signal_with_target() -> Signal:
    return Signal(
        signal_id="sig-1",
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        confidence=0.8,
        horizon="5m",
        suggested_entry=Decimal(100),
        suggested_stop=Decimal(98),
        suggested_target=Decimal(104),
        regime="TRENDING_UP",
        strategy="test",
        agent="test",
        rationale="unit",
        generated_at=datetime.now(UTC),
    )


def test_sizer_propagates_target_price_to_intent() -> None:
    sizer = PositionSizer(risk_per_trade_pct=0.01, default_stop_pct=0.02)
    state = PortfolioState(equity=Decimal(10000), daily_pnl=Decimal(0))
    decision = RiskDecision(
        signal_id="s1", action=RiskAction.APPROVE, reason="ok", max_leverage=10,
    )
    intent = sizer.size(_signal_with_target(), decision, state)
    assert intent.stop_price == Decimal(98)
    assert intent.target_price == Decimal(104)


# ---------------------------------------------------------------- execution
def _entry_order(*, side: OrderSide = OrderSide.BUY) -> Order:
    return Order(
        exchange_order_id="123",
        client_order_id="ai-abc",
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        side=side,
        type=OrderType.MARKET,
        status=OrderStatus.FILLED,
        quantity=Decimal("82.11"),
        executed_quantity=Decimal("82.11"),
        average_price=Decimal("121.78"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        fills=[
            OrderFill(
                price=Decimal("121.78"),
                quantity=Decimal("82.11"),
                commission=Decimal("3.99"),
                commission_asset="USDT",
                timestamp=datetime.now(UTC),
                trade_id="95758881",
            )
        ],
    )


def _intent(
    *,
    side: OrderSide = OrderSide.BUY,
    stop: Decimal | None = Decimal("121.28"),
    target: Decimal | None = Decimal("122.61"),
) -> OrderIntent:
    return OrderIntent(
        signal_id="sig",
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        side=side,
        quantity=Decimal("82.11"),
        order_type=OrderType.MARKET,
        stop_price=stop,
        target_price=target,
        reason="unit",
        agent="test",
    )


def _make_agent(
    conditional_mock: AsyncMock,
    *,
    is_hedge: bool = False,
) -> ExecutionAgent:
    context = MagicMock()
    context.event_bus.publish = AsyncMock()
    context.exchange.orders.place_conditional_order = conditional_mock
    context.exchange.orders.place_order = AsyncMock()
    context.exchange.positions.is_hedge_mode = AsyncMock(return_value=is_hedge)

    symbol_info = MagicMock()
    symbol_info.round_price.side_effect = lambda sym, mk, p: p
    symbol_info.round_quantity.side_effect = lambda sym, mk, q: q
    return ExecutionAgent(context, symbol_info=symbol_info)


def _fake_conditional(req: OrderRequest) -> Order:
    return Order(
        exchange_order_id="algo-1",
        client_order_id=req.client_order_id,
        symbol=req.symbol,
        market_type=req.market_type,
        side=req.side,
        type=req.type,
        status=OrderStatus.NEW,
        quantity=req.quantity,
        executed_quantity=Decimal(0),
        stop_price=req.stop_price,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def test_place_stop_loss_after_fill_one_way() -> None:
    captured: list[OrderRequest] = []

    async def fake_place(req: OrderRequest) -> Order:
        captured.append(req)
        return _fake_conditional(req)

    agent = _make_agent(AsyncMock(side_effect=fake_place), is_hedge=False)
    await agent._place_protective_orders(
        _intent(target=None), _entry_order(), send_sl=True, send_tp=True,
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.type is OrderType.STOP_MARKET
    assert req.side is OrderSide.SELL
    assert req.close_position is True
    assert req.position_side is None
    assert req.stop_price == Decimal("121.28")


async def test_place_take_profit_after_fill_one_way() -> None:
    captured: list[OrderRequest] = []

    async def fake_place(req: OrderRequest) -> Order:
        captured.append(req)
        return _fake_conditional(req)

    agent = _make_agent(AsyncMock(side_effect=fake_place), is_hedge=False)
    await agent._place_protective_orders(
        _intent(stop=None), _entry_order(), send_sl=True, send_tp=True,
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.type is OrderType.TAKE_PROFIT_MARKET
    assert req.close_position is True
    assert req.stop_price == Decimal("122.61")


async def test_hedge_mode_uses_position_side_not_close_position() -> None:
    captured: list[OrderRequest] = []

    async def fake_place(req: OrderRequest) -> Order:
        captured.append(req)
        return _fake_conditional(req)

    agent = _make_agent(AsyncMock(side_effect=fake_place), is_hedge=True)
    await agent._place_protective_orders(
        _intent(target=None),
        _entry_order(side=OrderSide.BUY),
        send_sl=True,
        send_tp=True,
    )

    req = captured[0]
    assert req.close_position is False
    assert req.position_side is PositionSide.LONG


async def test_skip_protection_when_intent_has_no_prices() -> None:
    place = AsyncMock()
    agent = _make_agent(place, is_hedge=False)
    await agent._place_protective_orders(
        _intent(stop=None, target=None),
        _entry_order(),
        send_sl=True,
        send_tp=True,
    )
    place.assert_not_called()


async def test_stop_loss_failure_does_not_raise() -> None:
    async def boom(req: OrderRequest) -> Order:
        raise RuntimeError("binance reject")

    agent = _make_agent(AsyncMock(side_effect=boom), is_hedge=False)
    await agent._place_protective_orders(
        _intent(target=None), _entry_order(), send_sl=True, send_tp=True,
    )