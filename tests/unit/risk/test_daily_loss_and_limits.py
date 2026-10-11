"""R1 do endurecimento do risco: perda diária real (absoluta e % do equity),
perdas seguidas e exposição total, mais o carregador de estado."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarginType, MarketType, PositionSide, RiskAction, SignalDirection
from app.domain.models.position import FuturesPosition
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.portfolio.state_loader import day_start_utc, load_portfolio_state
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits

NOW = datetime(2026, 10, 10, 15, 30, tzinfo=UTC)


def _signal(symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        signal_id="s1", symbol=symbol, market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG, confidence=0.8, horizon="5m",
        strategy="momentum", agent="asset::BTCUSDT", generated_at=NOW,
    )


def _position(symbol: str, qty: str, mark: str, pnl: str = "0") -> FuturesPosition:
    return FuturesPosition(
        symbol=symbol, position_side=PositionSide.LONG, quantity=Decimal(qty),
        entry_price=Decimal(mark), mark_price=Decimal(mark), unrealized_pnl=Decimal(pnl),
        realized_pnl=Decimal(0), leverage=1, margin_type=MarginType.CROSSED,
        isolated_margin=Decimal(0), liquidation_price=None, updated_at=NOW,
    )


def _state(**kw) -> PortfolioState:
    base = {"equity": Decimal(3000), "daily_pnl": Decimal(0), "positions": []}
    return PortfolioState(**{**base, **kw})


def _engine(**kw) -> RiskEngine:
    return RiskEngine(RiskLimits(**kw))


# ---------------------------------------------------------- perda diária
def test_daily_limit_is_the_stricter_of_absolute_and_pct():
    engine = _engine(max_daily_loss=Decimal(1000), max_daily_loss_pct=Decimal(3))
    assert engine.daily_loss_limit(Decimal(3000)) == Decimal(90)
    # % desligado → vale o absoluto
    assert _engine(max_daily_loss=Decimal(1000)).daily_loss_limit(Decimal(3000)) == 1000


@pytest.mark.parametrize(("pnl", "action"), [("-80", RiskAction.APPROVE), ("-90", RiskAction.REJECT)])
async def test_pct_daily_loss_blocks_at_limit(pnl, action):
    engine = _engine(max_daily_loss_pct=Decimal(3))
    decision = await engine.evaluate(_signal(), _state(daily_pnl=Decimal(pnl)))
    assert decision.action is action
    if action is RiskAction.REJECT:
        assert "daily loss limit" in decision.reason


# --------------------------------------------------------- perdas seguidas
@pytest.mark.parametrize(("streak", "action"), [(3, RiskAction.APPROVE), (4, RiskAction.REJECT)])
async def test_consecutive_losses(streak, action):
    engine = _engine(max_consecutive_losses=4)
    decision = await engine.evaluate(_signal(), _state(consecutive_losses=streak))
    assert decision.action is action


async def test_consecutive_losses_zero_disables():
    decision = await _engine().evaluate(_signal(), _state(consecutive_losses=99))
    assert decision.action is RiskAction.APPROVE


# --------------------------------------------------------- exposição total
async def test_total_notional_counts_a_full_new_position():
    engine = _engine(
        max_position_notional_per_symbol=Decimal(2000), max_total_notional=Decimal(5000)
    )
    # 3.100 abertos + até 2.000 novos = 5.100 > 5.000
    open_pos = [_position("SOLUSDT", "10", "310")]
    decision = await engine.evaluate(_signal(), _state(positions=open_pos))
    assert decision.action is RiskAction.REJECT
    assert "max_total_notional" in decision.reason

    small = [_position("SOLUSDT", "5", "200")]  # 1.000 + 2.000 <= 5.000
    assert (await engine.evaluate(_signal(), _state(positions=small))).action is RiskAction.APPROVE


# ------------------------------------------------------------- carregador
class _Result:
    def __init__(self, scalar=None, rows=None) -> None:
        self._scalar, self._rows = scalar, rows or []

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, realized, recent) -> None:
        self._results = [_Result(scalar=realized), _Result(rows=recent)]
        self.statements: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results[len(self.statements) - 1]


def _exchange(positions, total="3000"):
    ex = MagicMock()
    ex.account.get_futures_positions = AsyncMock(return_value=positions)
    ex.account.get_futures_balance = AsyncMock(return_value=MagicMock(total=Decimal(total)))
    return ex


async def test_loader_sums_realized_today_and_unrealized_and_counts_streak():
    session = _Session(
        realized=Decimal("-40.5"),
        recent=[Decimal(-3), Decimal(-1), Decimal("-0.5"), Decimal(2), Decimal(-9)],
    )
    exchange = _exchange([_position("XRPUSDT", "100", "1.4", pnl="-12.25")])

    state = await load_portfolio_state(exchange, lambda: session, now=NOW)

    assert state.equity == Decimal(3000)
    assert state.daily_pnl == Decimal("-52.75")  # -40.5 realizado + -12.25 aberto
    assert state.consecutive_losses == 3  # para no primeiro trip sem prejuízo
    realized_sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "2026-10-10 00:00:00" in realized_sql  # janela começa à 00:00 UTC


async def test_loader_without_db_uses_only_unrealized():
    exchange = _exchange([_position("XRPUSDT", "100", "1.4", pnl="5")])
    state = await load_portfolio_state(exchange, None, now=NOW)
    assert state.daily_pnl == Decimal(5)
    assert state.consecutive_losses == 0


def test_day_start_is_utc_midnight():
    from datetime import timedelta, timezone

    brt = datetime(2026, 10, 9, 22, 0, tzinfo=timezone(timedelta(hours=-3)))  # 01:00 UTC do dia 10
    assert day_start_utc(brt) == datetime(2026, 10, 10, tzinfo=UTC)
