"""R2: trava de trading (halt) persistente e intervenção humana."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.risk import RiskAgent
from app.core.enums import HaltScope, HaltSource, MarketType, RiskAction, SignalDirection
from app.domain.models.signal import Signal
from app.events.event import HealthCheckFailed
from app.portfolio.state import HaltInfo, PortfolioState
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits
from app.runtime import trading_control

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _signal(symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        signal_id="s1", symbol=symbol, market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG, confidence=0.8, horizon="5m",
        strategy="momentum", agent=f"asset::{symbol}", generated_at=NOW,
    )


def _state(halts=(), **kw) -> PortfolioState:
    base = {"equity": Decimal(3000), "daily_pnl": Decimal(0), "active_halts": list(halts)}
    return PortfolioState(**{**base, **kw})


GLOBAL = HaltInfo(scope="GLOBAL", rule="manual", reason="manutenção")
XRP = HaltInfo(scope="SYMBOL", symbol="XRPUSDT", rule="manual", reason="spread alto")


# ------------------------------------------------------------- engine
async def test_global_halt_blocks_every_symbol_first():
    engine = RiskEngine(RiskLimits(min_confidence=0.99))  # confiança também falharia
    decision = await engine.evaluate(_signal(), _state([GLOBAL]))
    assert decision.action is RiskAction.REJECT
    assert decision.rule == "halted"
    assert decision.reason == "TRADING HALTED (global): manutenção"


async def test_symbol_halt_blocks_only_that_symbol():
    engine = RiskEngine(RiskLimits())
    assert (await engine.evaluate(_signal("XRPUSDT"), _state([XRP]))).rule == "halted"
    assert (await engine.evaluate(_signal("BTCUSDT"), _state([XRP]))).action is RiskAction.APPROVE


async def test_rejections_carry_their_rule():
    engine = RiskEngine(RiskLimits(max_daily_loss_pct=Decimal(3)))
    decision = await engine.evaluate(_signal(), _state(daily_pnl=Decimal(-100)))
    assert decision.rule == "daily_loss"


# ---------------------------------------------------------- auto-halt
class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self) -> None:
        pass


@pytest.fixture
def risk_agent(monkeypatch):
    calls: list[dict] = []
    created = {"value": object()}  # sentinela: trava criada

    async def create_if_absent(_self, **kw):
        calls.append(kw)
        return created["value"]

    monkeypatch.setattr(
        "app.database.repositories.trading_halt.TradingHaltRepository.create_if_absent", create_if_absent
    )
    context = MagicMock()
    context.session_factory = _Session
    context.event_bus.publish = AsyncMock()
    agent = RiskAgent(
        context, engine=RiskEngine(RiskLimits(max_daily_loss_pct=Decimal(3), max_consecutive_losses=4))
    )
    return agent, calls, created


async def test_daily_loss_creates_global_auto_halt_and_alerts(risk_agent):
    agent, calls, _ = risk_agent

    decision = await agent.evaluate(_signal(), _state(daily_pnl=Decimal(-95)))

    assert decision.rule == "daily_loss"
    (call,) = calls
    assert call["scope"] is HaltScope.GLOBAL and call["symbol"] is None
    assert call["source"] is HaltSource.AUTO and call["rule"] == "daily_loss"
    (event,) = [c.args[0] for c in agent.context.event_bus.publish.await_args_list]
    assert isinstance(event, HealthCheckFailed) and event.component == "trading_halt"


async def test_consecutive_losses_also_halts(risk_agent):
    agent, calls, _ = risk_agent
    await agent.evaluate(_signal(), _state(consecutive_losses=4))
    assert calls[0]["rule"] == "consecutive_losses"


async def test_existing_halt_is_not_duplicated_nor_realerted(risk_agent):
    agent, _, created = risk_agent
    created["value"] = None  # repositório: já havia trava ativa
    await agent.evaluate(_signal(), _state(daily_pnl=Decimal(-95)))
    agent.context.event_bus.publish.assert_not_awaited()


async def test_other_rejections_do_not_halt(risk_agent):
    agent, calls, _ = risk_agent
    agent._engine = RiskEngine(RiskLimits(min_confidence=0.99))
    decision = await agent.evaluate(_signal(), _state())
    assert decision.rule == "confidence"
    assert calls == []


async def test_auto_halt_db_failure_is_logged_not_raised(risk_agent, monkeypatch, caplog):
    agent, _, _ = risk_agent

    async def boom(_self, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.database.repositories.trading_halt.TradingHaltRepository.create_if_absent", boom)
    decision = await agent.evaluate(_signal(), _state(daily_pnl=Decimal(-95)))
    assert decision.action is RiskAction.REJECT
    assert "halt_guard.trip_failed" in caplog.text


# ------------------------------------------------------ trading_control
async def test_pause_requires_reason():
    with pytest.raises(ValueError):
        await trading_control.pause(MagicMock(), reason="  ", by="me")


async def test_pause_and_resume_scope_and_symbol(monkeypatch):
    created: list[dict] = []
    cleared: list[dict] = []

    async def create_if_absent(_self, **kw):
        created.append(kw)
        return object()

    async def clear(_self, **kw):
        cleared.append(kw)
        return 2

    repo = "app.runtime.trading_control.TradingHaltRepository"
    monkeypatch.setattr(f"{repo}.create_if_absent", create_if_absent)
    monkeypatch.setattr(f"{repo}.clear", clear)

    await trading_control.pause(_Session, reason="spread", by="irlan", symbol="xrpusdt")
    n = await trading_control.resume(_Session, by="irlan", note="ok")

    assert created[0]["scope"] is HaltScope.SYMBOL and created[0]["symbol"] == "XRPUSDT"
    assert created[0]["source"] is HaltSource.MANUAL and created[0]["rule"] == "manual"
    assert cleared[0]["scope"] is HaltScope.GLOBAL and cleared[0]["symbol"] is None
    assert n == 2
