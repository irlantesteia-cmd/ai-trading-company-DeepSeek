"""Segurança das ordens de proteção: SL/TP ancorados no fill e fechamento
imediato quando o stop-loss não pode ser criado (teste ao vivo de 2026-10-09:
`-2021 Order would immediately trigger` deixou a posição sem stop)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

from app.core.config import settings
from app.core.enums import OrderSide, OrderType
from app.core.exceptions import ExchangeOrderRejectedError
from app.domain.models.order import Order, OrderRequest
from app.events.event import HealthCheckFailed, OrderFilled
from app.portfolio.sizing import PositionSizer
from tests.unit.test_protective_orders import (
    _entry_order,
    _fake_conditional,
    _intent,
    _make_agent,
    _signal_with_target,
)


def _captured_agent(*, fail_types: frozenset[OrderType] = frozenset()):
    captured: list[OrderRequest] = []

    async def fake_place(req: OrderRequest) -> Order:
        captured.append(req)
        if req.type in fail_types:
            raise ExchangeOrderRejectedError("-2021: Order would immediately trigger.")
        return _fake_conditional(req)

    agent = _make_agent(AsyncMock(side_effect=fake_place), is_hedge=False)
    return agent, captured


def _published(agent, event_type):
    return [
        c.args[0]
        for c in agent.context.event_bus.publish.await_args_list
        if isinstance(c.args[0], event_type)
    ]


# ------------------------------------------------------------ ancoragem
async def test_long_levels_are_anchored_to_fill_keeping_distance():
    # Sinal no close 121.50; fill saiu a 121.78 (+0.28).
    intent = _intent(stop=Decimal("121.00"), target=Decimal("122.50")).model_copy(
        update={"reference_price": Decimal("121.50")}
    )
    agent, captured = _captured_agent()

    await agent._place_protective_orders(intent, _entry_order(), send_sl=True, send_tp=True)

    sl, tp = captured
    assert sl.type is OrderType.STOP_MARKET and sl.stop_price == Decimal("121.28")
    assert tp.type is OrderType.TAKE_PROFIT_MARKET and tp.stop_price == Decimal("122.78")


async def test_short_levels_are_anchored_to_fill():
    intent = _intent(
        side=OrderSide.SELL, stop=Decimal("122.00"), target=Decimal("120.50")
    ).model_copy(update={"reference_price": Decimal("121.50")})
    agent, captured = _captured_agent()

    await agent._place_protective_orders(
        intent, _entry_order(side=OrderSide.SELL), send_sl=True, send_tp=True
    )

    sl, tp = captured
    assert sl.side is OrderSide.BUY
    assert sl.stop_price == Decimal("122.28")  # fill 121.78 + 0.50
    assert tp.stop_price == Decimal("120.78")  # fill 121.78 - 1.00


async def test_without_reference_price_levels_are_unchanged():
    agent, captured = _captured_agent()
    await agent._place_protective_orders(
        _intent(target=None), _entry_order(), send_sl=True, send_tp=True
    )
    assert captured[0].stop_price == Decimal("121.28")


def test_sizer_sets_reference_price_to_signal_entry():
    from app.core.enums import RiskAction
    from app.domain.models.risk import RiskDecision
    from app.portfolio.state import PortfolioState

    signal = _signal_with_target()
    intent = PositionSizer().size(
        signal,
        RiskDecision(signal_id="s1", action=RiskAction.APPROVE, reason="ok", max_leverage=10),
        PortfolioState(equity=Decimal(10000), daily_pnl=Decimal(0), positions=[]),
    )
    assert intent.reference_price == signal.suggested_entry


# ------------------------------------------------- falha no stop-loss
async def test_stop_loss_failure_flattens_position_and_skips_take_profit():
    agent, captured = _captured_agent(fail_types=frozenset({OrderType.STOP_MARKET}))
    close_order = _entry_order(side=OrderSide.SELL).model_copy(update={"exchange_order_id": "999"})
    agent.context.exchange.positions.close_position = AsyncMock(return_value=close_order)

    await agent._place_protective_orders(_intent(), _entry_order(), send_sl=True, send_tp=True)

    assert [r.type for r in captured] == [OrderType.STOP_MARKET]  # TP não enviado
    agent.context.exchange.positions.close_position.assert_awaited_once_with("SOLUSDT", None)
    (alert,) = _published(agent, HealthCheckFailed)
    assert alert.component == "protection" and "fechada" in alert.detail
    (fill,) = _published(agent, OrderFilled)
    assert fill.exchange_order_id == "999"


async def test_flatten_failure_raises_alert_and_does_not_raise():
    agent, _ = _captured_agent(fail_types=frozenset({OrderType.STOP_MARKET}))
    agent.context.exchange.positions.close_position = AsyncMock(side_effect=RuntimeError("down"))

    await agent._place_protective_orders(_intent(), _entry_order(), send_sl=True, send_tp=True)

    (alert,) = _published(agent, HealthCheckFailed)
    assert "fechamento também falhou" in alert.detail
    assert _published(agent, OrderFilled) == []


async def test_setting_off_keeps_position_and_still_sends_take_profit(monkeypatch):
    monkeypatch.setattr(settings, "close_on_stop_loss_failure", False)
    agent, captured = _captured_agent(fail_types=frozenset({OrderType.STOP_MARKET}))
    agent.context.exchange.positions.close_position = AsyncMock()

    await agent._place_protective_orders(_intent(), _entry_order(), send_sl=True, send_tp=True)

    agent.context.exchange.positions.close_position.assert_not_awaited()
    assert [r.type for r in captured] == [OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET]


async def test_take_profit_failure_alone_does_not_flatten():
    agent, captured = _captured_agent(fail_types=frozenset({OrderType.TAKE_PROFIT_MARKET}))
    agent.context.exchange.positions.close_position = AsyncMock()

    await agent._place_protective_orders(_intent(), _entry_order(), send_sl=False, send_tp=True)

    # Falha só no TP não fecha a posição.
    agent.context.exchange.positions.close_position.assert_not_awaited()
    assert [r.type for r in captured] == [OrderType.TAKE_PROFIT_MARKET]
