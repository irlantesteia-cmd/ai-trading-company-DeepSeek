"""Testes da regra `max_same_direction` do RiskEngine."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.enums import RiskAction
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits


def _signal(
    *,
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    confidence: float = 0.7,
) -> MagicMock:
    sig = MagicMock()
    sig.signal_id = "sig-test"
    sig.symbol = symbol
    sig.direction = direction
    sig.confidence = confidence
    return sig


def _state(
    *,
    count_in_direction: int = 0,
    open_positions_count: int = 0,
    daily_pnl: Decimal | None = None,
    has_position: bool = False,
    symbols: set[str] | None = None,
) -> MagicMock:
    st = MagicMock()
    st.daily_pnl = daily_pnl if daily_pnl is not None else Decimal(0)
    st.open_positions_count = open_positions_count
    st.has_position = MagicMock(return_value=has_position)
    st.symbols = MagicMock(return_value=symbols or set())
    st.count_in_direction = MagicMock(return_value=count_in_direction)
    return st


def _limits(*, max_same_direction: int = 2) -> RiskLimits:
    return RiskLimits(
        max_position_notional_per_symbol=Decimal(10000),
        max_total_notional=Decimal(50000),
        max_leverage=10,
        max_daily_loss=Decimal(1000),
        max_open_positions=5,
        max_same_direction=max_same_direction,
        min_confidence=0.5,
        correlated_groups={},
    )


@pytest.mark.asyncio
async def test_rejects_when_direction_limit_reached() -> None:
    engine = RiskEngine(_limits(max_same_direction=2))
    sig = _signal(direction="SHORT")
    st = _state(count_in_direction=2)

    decision = await engine.evaluate(sig, st)

    assert decision.action == RiskAction.REJECT
    assert "max_same_direction" in decision.reason
    assert "SHORT" in decision.reason


@pytest.mark.asyncio
async def test_accepts_when_direction_has_room() -> None:
    engine = RiskEngine(_limits(max_same_direction=2))
    sig = _signal(direction="SHORT")
    st = _state(count_in_direction=1)

    decision = await engine.evaluate(sig, st)

    assert decision.action == RiskAction.APPROVE


@pytest.mark.asyncio
async def test_long_query_is_independent_of_shorts() -> None:
    """O engine consulta a contagem **pela direção do sinal**, não por total."""
    engine = RiskEngine(_limits(max_same_direction=2))
    sig = _signal(direction="LONG")
    st = _state(count_in_direction=0)  # simula 0 LONGs (mas 2 SHORTs abertos)

    decision = await engine.evaluate(sig, st)

    assert decision.action == RiskAction.APPROVE
    st.count_in_direction.assert_called_once_with("LONG")


@pytest.mark.asyncio
async def test_empty_state_approves() -> None:
    engine = RiskEngine(_limits(max_same_direction=2))
    sig = _signal(direction="LONG")
    st = _state()

    decision = await engine.evaluate(sig, st)

    assert decision.action == RiskAction.APPROVE


@pytest.mark.asyncio
async def test_higher_limit_allows_more() -> None:
    engine = RiskEngine(_limits(max_same_direction=3))
    sig = _signal(direction="SHORT")
    st = _state(count_in_direction=2)

    decision = await engine.evaluate(sig, st)

    assert decision.action == RiskAction.APPROVE