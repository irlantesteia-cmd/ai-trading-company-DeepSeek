"""Fase 5b: alavancagem explícita aplicada no boot."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.core.exceptions import ExchangeError
from app.runtime.leverage import apply_leverage_on_boot


async def test_applies_leverage_to_every_traded_symbol():
    exchange = MagicMock()
    exchange.positions.set_leverage = AsyncMock()
    result = await apply_leverage_on_boot(exchange, ["BTCUSDT", "ETHUSDT"], 3)
    assert result == {"BTCUSDT": True, "ETHUSDT": True}
    assert [c.args for c in exchange.positions.set_leverage.await_args_list] == [
        ("BTCUSDT", 3), ("ETHUSDT", 3),
    ]


async def test_failure_halts_only_that_symbol_and_keeps_going():
    exchange = MagicMock()

    async def set_leverage(symbol, leverage):
        if symbol == "SOLUSDT":
            raise ExchangeError("-4028 leverage not valid")

    exchange.positions.set_leverage = set_leverage
    guard = MagicMock()
    guard.trip = AsyncMock(return_value=True)
    result = await apply_leverage_on_boot(exchange, ["SOLUSDT", "XRPUSDT"], 3, guard=guard)
    assert result == {"SOLUSDT": False, "XRPUSDT": True}
    (call,) = guard.trip.await_args_list
    assert call.kwargs["rule"] == "leverage" and call.kwargs["symbol"] == "SOLUSDT"
