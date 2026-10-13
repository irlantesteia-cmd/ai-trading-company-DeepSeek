"""Fase 5c: limite de drawdown a partir do pico do capital."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.core.enums import MarginType, MarketType, PositionSide, RiskAction, SignalDirection
from app.domain.models.position import FuturesPosition
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.portfolio.state_loader import load_portfolio_state
from app.risk.engine import AUTO_HALT_RULES, RiskEngine
from app.risk.limits import RiskLimits
from app.runtime.equity_recorder import record_equity_snapshot
from tests.unit.risk.test_daily_loss_and_limits import _Session

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)
LIMITS = RiskLimits(max_drawdown_pct=Decimal(10))


def _signal() -> Signal:
    return Signal(signal_id="s1", symbol="BTCUSDT", market_type=MarketType.FUTURES,
                  direction=SignalDirection.LONG, confidence=0.9, horizon="5m",
                  strategy="momentum", agent="a", generated_at=NOW)


def _position(pnl: str) -> FuturesPosition:
    return FuturesPosition(symbol="ETHUSDT", position_side=PositionSide.LONG, quantity=Decimal(1),
                           entry_price=Decimal(100), mark_price=Decimal(100), unrealized_pnl=Decimal(pnl),
                           realized_pnl=Decimal(0), leverage=3, margin_type=MarginType.CROSSED,
                           isolated_margin=Decimal(0), updated_at=NOW)


def _state(equity: str, peak: str | None, positions=()) -> PortfolioState:
    return PortfolioState(equity=Decimal(equity), daily_pnl=Decimal(0), positions=list(positions),
                          peak_equity=None if peak is None else Decimal(peak))


def test_drawdown_counts_unrealized_pnl():
    state = _state("2900", "3200", [_position("-20")])  # 2.880 contra pico 3.200
    assert state.total_equity == Decimal(2880)
    assert state.drawdown_pct == Decimal(10)
    assert _state("3000", None).drawdown_pct == 0


async def test_drawdown_rule_rejects_at_the_limit_and_is_an_auto_halt():
    engine = RiskEngine(LIMITS)
    assert (await engine.evaluate(_signal(), _state("2881", "3200"))).action is RiskAction.APPROVE
    no = await engine.evaluate(_signal(), _state("2880", "3200"))
    assert no.action is RiskAction.REJECT and no.rule == "max_drawdown"
    assert "max_drawdown" in AUTO_HALT_RULES
    off = RiskEngine(RiskLimits())
    assert (await off.evaluate(_signal(), _state("100", "3200"))).action is RiskAction.APPROVE


def _exchange(total: str, pnl: str = "0"):
    ex = MagicMock()
    ex.account.get_futures_positions = AsyncMock(return_value=[_position(pnl)] if pnl != "0" else [])
    ex.account.get_futures_balance = AsyncMock(return_value=MagicMock(total=Decimal(total), free=Decimal(total)))
    return ex


async def test_loader_peak_includes_current_and_restarts_after_resume():
    session = _Session(realized=Decimal(0), recent=[], peak=Decimal(3100))
    state = await load_portfolio_state(_exchange("3000"), lambda: session, now=NOW)
    assert state.peak_equity == Decimal(3100)
    sql = str(session.statements[3].compile(dialect=postgresql.dialect(),
                                            compile_kwargs={"literal_binds": True}))
    assert "max(equity_snapshots.total_equity)" in sql
    assert "trading_halts.rule = 'max_drawdown'" in sql and "equity_snapshots.taken_at >" in sql

    higher = _Session(realized=Decimal(0), recent=[], peak=Decimal(3100))
    state = await load_portfolio_state(_exchange("3300"), lambda: higher, now=NOW)
    assert state.peak_equity == Decimal(3300)  # o capital de agora conta como pico

    empty = _Session(realized=Decimal(0), recent=[], peak=None)
    state = await load_portfolio_state(_exchange("3000"), lambda: empty, now=NOW)
    assert state.peak_equity is None and state.drawdown_pct == 0


async def test_equity_snapshot_records_wallet_plus_unrealized():
    added = []

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def add(self, obj):
            added.append(obj)

        async def commit(self):
            pass

    snap = await record_equity_snapshot(_exchange("3000", "-25"), _S, now=NOW)
    assert snap is not None and added == [snap]
    assert snap.total_equity == Decimal(2975) and snap.unrealized_pnl == Decimal(-25)


async def test_equity_snapshot_failure_is_logged_not_raised(caplog):
    ex = MagicMock()
    ex.account.get_futures_balance = AsyncMock(side_effect=RuntimeError("down"))
    assert await record_equity_snapshot(ex, MagicMock()) is None
    assert "equity_snapshot.failed" in caplog.text
