"""Monta o `PortfolioState` usado pelo risco e pelo dimensionamento.

Antes, TradingManager, RiskAgent e PortfolioAgent montavam o estado cada um
com `daily_pnl=0` fixo, então o limite de perda diária nunca disparava.

- posições e saldo: exchange;
- `daily_pnl`: P&L líquido realizado desde 00:00 UTC (`round_trips` fechados)
  + P&L não realizado das posições abertas;
- `consecutive_losses`: round trips fechados com `net_pnl < 0` em sequência,
  do mais recente para trás;
- `active_halts`: travas de trading ativas (`trading_halts`).

Sem `session_factory` (testes, scripts), a parte do banco vale zero. Falha
ao ler o banco **propaga**: sem saber a perda do dia, o sinal não deve ser
executado (o TradingManager registra e descarta).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import RoundTripStatus
from app.database.models.round_trip import RoundTripORM
from app.database.repositories.trading_halt import TradingHaltRepository
from app.exchanges.base.exchange import Exchange
from app.portfolio.state import HaltInfo, PortfolioState

# Janela para contar perdas seguidas: mais que qualquer limite razoável.
_STREAK_LOOKBACK = 50


def day_start_utc(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def load_portfolio_state(
    exchange: Exchange,
    session_factory: async_sessionmaker | None,
    *,
    now: datetime | None = None,
) -> PortfolioState:
    positions = await exchange.account.get_futures_positions()
    balance = await exchange.account.get_futures_balance("USDT")
    unrealized = sum((p.unrealized_pnl for p in positions if p.is_open), Decimal(0))

    realized_today = Decimal(0)
    consecutive_losses = 0
    halts: list[HaltInfo] = []
    if session_factory is not None:
        realized_today, consecutive_losses = await _realized_stats(
            session_factory, day_start_utc(now or datetime.now(UTC))
        )
        halts = await _active_halts(session_factory)

    return PortfolioState(
        equity=balance.total,
        daily_pnl=realized_today + unrealized,
        positions=positions,
        consecutive_losses=consecutive_losses,
        active_halts=halts,
    )


async def _active_halts(session_factory: async_sessionmaker) -> list[HaltInfo]:
    async with session_factory() as session:
        rows = await TradingHaltRepository(session).list_active()
    return [
        HaltInfo(scope=r.scope, symbol=r.symbol, rule=r.rule, reason=r.reason)
        for r in rows
    ]


async def _realized_stats(
    session_factory: async_sessionmaker, day_start: datetime
) -> tuple[Decimal, int]:
    closed = RoundTripORM.status == RoundTripStatus.CLOSED.value
    realized_stmt = select(func.coalesce(func.sum(RoundTripORM.net_pnl), 0)).where(
        closed, RoundTripORM.closed_at >= day_start
    )
    recent_stmt = (
        select(RoundTripORM.net_pnl)
        .where(closed, RoundTripORM.net_pnl.is_not(None))
        .order_by(RoundTripORM.closed_at.desc())
        .limit(_STREAK_LOOKBACK)
    )
    async with session_factory() as session:
        realized = Decimal(str((await session.execute(realized_stmt)).scalar_one()))
        recent = list((await session.execute(recent_stmt)).scalars().all())

    streak = 0
    for pnl in recent:
        if pnl >= 0:
            break
        streak += 1
    return realized, streak
