"""Fase 5a: exposição total relativa ao capital e checagem de margem."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.core.enums import MarginType, MarketType, PositionSide, RiskAction, SignalDirection
from app.domain.models.position import FuturesPosition
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.portfolio.state_loader import load_portfolio_state
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def _signal(symbol: str = "ETHUSDT") -> Signal:
    return Signal(signal_id="s1", symbol=symbol, market_type=MarketType.FUTURES,
                  direction=SignalDirection.LONG, confidence=0.9, horizon="5m",
                  strategy="momentum", agent="a", generated_at=NOW)


def _position(symbol: str, notional: str) -> FuturesPosition:
    return FuturesPosition(symbol=symbol, position_side=PositionSide.LONG, quantity=Decimal(1),
                           entry_price=Decimal(notional), mark_price=Decimal(notional),
                           unrealized_pnl=Decimal(0), realized_pnl=Decimal(0), leverage=3,
                           margin_type=MarginType.CROSSED, isolated_margin=Decimal(0), updated_at=NOW)


def _state(equity="3000", available=None, positions=()) -> PortfolioState:
    return PortfolioState(equity=Decimal(equity), daily_pnl=Decimal(0), positions=list(positions),
                          available_balance=None if available is None else Decimal(available))


LIMITS = RiskLimits(max_position_notional_per_symbol=Decimal(2000), max_open_positions=10,
                    max_same_direction=10, max_total_exposure_x=Decimal(2), margin_buffer=Decimal("1.2"),
                    max_leverage=3)


async def test_total_exposure_is_relative_to_equity():
    engine = RiskEngine(LIMITS)
    # 3.000 de equity → teto 6.000; com 4.000 abertos, mais 2.000 ainda cabe
    ok = await engine.evaluate(_signal(), _state(positions=[_position("BTCUSDT", "4000")]))
    assert ok.action is RiskAction.APPROVE
    # 4.500 abertos + 2.000 = 6.500 > 6.000
    no = await engine.evaluate(_signal(), _state(positions=[_position("BTCUSDT", "4500")]))
    assert no.action is RiskAction.REJECT and no.rule == "total_exposure"
    assert "2" in no.reason and "equity" in no.reason


async def test_exposure_rule_off_with_zero():
    engine = RiskEngine(RiskLimits(max_position_notional_per_symbol=Decimal(2000), max_open_positions=10,
                                   max_same_direction=10))
    decision = await engine.evaluate(_signal(), _state(positions=[_position("BTCUSDT", "40000")]))
    assert decision.action is RiskAction.APPROVE  # só o teto absoluto padrão (50.000)


async def test_margin_check_uses_configured_leverage_and_buffer():
    engine = RiskEngine(LIMITS)
    # margem exigida = 2.000 / 3 × 1,2 = 800
    assert (await engine.evaluate(_signal(), _state(available="800"))).action is RiskAction.APPROVE
    no = await engine.evaluate(_signal(), _state(available="799.99"))
    assert no.action is RiskAction.REJECT and no.rule == "margin"


async def test_margin_check_skipped_when_unknown_or_disabled():
    engine = RiskEngine(LIMITS)
    assert (await engine.evaluate(_signal(), _state(available=None))).action is RiskAction.APPROVE
    off = RiskEngine(RiskLimits(max_position_notional_per_symbol=Decimal(2000), max_leverage=3))
    assert (await off.evaluate(_signal(), _state(available="1"))).action is RiskAction.APPROVE


async def test_loader_fills_available_balance():
    ex = MagicMock()
    ex.account.get_futures_positions = AsyncMock(return_value=[])
    ex.account.get_futures_balance = AsyncMock(
        return_value=MagicMock(total=Decimal(3000), free=Decimal("2450.5")))
    state = await load_portfolio_state(ex, None, now=NOW)
    assert state.available_balance == Decimal("2450.5")
