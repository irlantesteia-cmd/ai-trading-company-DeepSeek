"""Fase 5d: validação de dados de mercado antes de calcular sinal."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.core.enums import MarketType
from app.market.candle_stream import CandleStreamService
from app.market.validation import candle_problem, interval_ms, window_problem
from tests.unit.agents.test_asset_agent_strategy import _AgentWithCandles, _AlwaysLong
from tests.unit.strategies.conftest import make_candles


def _broken(candles, index, **changes):
    candles = list(candles)
    candles[index] = candles[index].model_copy(update=changes)
    return candles


def test_interval_ms():
    assert interval_ms("5m") == 300_000 and interval_ms("4h") == 14_400_000
    assert interval_ms("1d") == 86_400_000 and interval_ms("xx") is None


def test_candle_problems():
    (ok,) = make_candles([100.0])
    assert candle_problem(ok) is None
    assert candle_problem(ok.model_copy(update={"low": Decimal(0)})) == "preço <= 0"
    assert "máxima" in candle_problem(ok.model_copy(update={"high": ok.close - 1}))
    assert "mínima" in candle_problem(ok.model_copy(update={"low": ok.close + 1, "high": ok.close + 2}))
    assert candle_problem(ok.model_copy(update={"volume": Decimal(-1)})) == "volume negativo"
    assert "fechamento" in candle_problem(ok.model_copy(update={"close_time": ok.open_time}))


def test_window_rejects_bad_candle_disorder_and_big_gaps_but_tolerates_small_gaps():
    candles = make_candles([100.0 + k for k in range(20)])  # 5m contínuos
    assert window_problem(candles, "5m") is None
    assert "preço" in window_problem(_broken(candles, 5, close=Decimal(0)), "5m")
    swapped = list(candles)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    assert "tempo não cresce" in window_problem(swapped, "5m")
    small_gap = candles[:10] + candles[12:]          # 2 barras faltando: só aviso
    assert window_problem(small_gap, "5m") is None
    big_gap = candles[:5] + candles[10:]             # índices 5 a 9: 5 barras faltando
    assert "5 barras faltando" in window_problem(big_gap, "5m")


def _agent(context, candles):
    return _AgentWithCandles(context, symbol="BTCUSDT", market_type=MarketType.FUTURES, interval="5m",
                             strategy=_AlwaysLong(), candles=candles)


async def test_agent_skips_invalid_window_and_halts_the_symbol_after_three(context, monkeypatch):
    trips: list[dict] = []

    async def trip(_self, **kw):
        trips.append(kw)
        return True

    monkeypatch.setattr("app.runtime.halt_guard.HaltGuard.trip", trip)
    monkeypatch.setattr(context.settings, "halt_market_data_runs", 3)
    bad = _broken(make_candles([100.0] * 10), 4, high=Decimal(1))
    agent = _agent(context, bad)
    assert await agent.analyze() is None
    assert await agent.analyze() is None
    assert trips == []
    assert await agent.analyze() is None
    (call,) = trips
    assert call["rule"] == "market_data" and call["symbol"] == "BTCUSDT"


async def test_valid_window_resets_the_count(context, monkeypatch):
    trips: list[dict] = []

    async def trip(_self, **kw):
        trips.append(kw)
        return True

    monkeypatch.setattr("app.runtime.halt_guard.HaltGuard.trip", trip)
    monkeypatch.setattr(context.settings, "halt_market_data_runs", 2)
    good = make_candles([100.0] * 10)
    bad = _broken(good, 4, high=Decimal(1))
    agent = _agent(context, bad)
    assert await agent.analyze() is None
    agent._candles = good
    assert await agent.analyze() is not None  # sinal volta e a contagem zera
    agent._candles = bad
    assert await agent.analyze() is None
    assert trips == []


async def test_stream_does_not_emit_an_incoherent_candle():
    candles = make_candles([100.0, 101.0, 102.0])
    candles = _broken(candles, 1, high=Decimal(1))  # o último fechado é inválido
    exchange = MagicMock()
    exchange.market_data.get_candles = AsyncMock(return_value=candles)
    bus = MagicMock()
    bus.publish = AsyncMock()
    stream = CandleStreamService(exchange=exchange, event_bus=bus, symbol="BTCUSDT", interval="5m",
                                 market_type=MarketType.FUTURES, max_emit_lag_s=0)
    now = candles[1].close_time + timedelta(seconds=1)
    assert await stream.poll_once(now=now) is None
    bus.publish.assert_not_awaited()
    assert stream.last_emitted_open_time == candles[1].open_time  # não tenta de novo
