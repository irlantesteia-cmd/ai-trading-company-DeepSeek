"""Intervenção humana sobre a trava de trading (`trading_halts`).

Usado por `scripts/trading_control.py`. O bot lê as travas a cada sinal
(via `load_portfolio_state`), então pausar/retomar vale na hora, sem
reiniciar. A trava só bloqueia **novas entradas**: posições abertas seguem
com SL, TP e saída por tempo.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import HaltScope, HaltSource
from app.database.models.trading_halt import TradingHaltORM
from app.database.repositories.trading_halt import TradingHaltRepository

MANUAL_RULE = "manual"


@dataclass(frozen=True)
class HaltStatus:
    active: list[TradingHaltORM]
    recent_cleared: list[TradingHaltORM]

    @property
    def halted_globally(self) -> bool:
        return any(h.scope == HaltScope.GLOBAL.value for h in self.active)


def _scope(symbol: str | None) -> HaltScope:
    return HaltScope.SYMBOL if symbol else HaltScope.GLOBAL


async def get_status(session_factory: async_sessionmaker, *, history: int = 10) -> HaltStatus:
    async with session_factory() as session:
        repo = TradingHaltRepository(session)
        return HaltStatus(
            active=await repo.list_active(),
            recent_cleared=await repo.list_recent_cleared(history),
        )


async def pause(
    session_factory: async_sessionmaker,
    *,
    reason: str,
    by: str,
    symbol: str | None = None,
) -> TradingHaltORM | None:
    """Cria trava manual. None se já havia trava manual ativa no escopo."""
    if not reason.strip():
        raise ValueError("informe o motivo da pausa (--reason)")
    async with session_factory() as session:
        halt = await TradingHaltRepository(session).create_if_absent(
            scope=_scope(symbol),
            symbol=symbol.upper() if symbol else None,
            source=HaltSource.MANUAL,
            rule=MANUAL_RULE,
            reason=reason.strip(),
            created_by=by,
        )
        await session.commit()
    return halt


async def resume(
    session_factory: async_sessionmaker,
    *,
    by: str,
    symbol: str | None = None,
    note: str | None = None,
) -> int:
    """Libera todas as travas ativas do escopo (manuais e automáticas).
    Retorna quantas foram liberadas."""
    async with session_factory() as session:
        cleared = await TradingHaltRepository(session).clear(
            scope=_scope(symbol),
            symbol=symbol.upper() if symbol else None,
            cleared_by=by,
            note=note,
        )
        await session.commit()
    return cleared
