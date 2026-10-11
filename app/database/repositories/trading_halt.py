from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CursorResult, func, select, update

from app.core.enums import HaltScope, HaltSource
from app.database.models.trading_halt import TradingHaltORM
from app.database.repositories.base import BaseRepository


class TradingHaltRepository(BaseRepository[TradingHaltORM]):
    model = TradingHaltORM

    async def list_active(self) -> list[TradingHaltORM]:
        stmt = (
            select(TradingHaltORM)
            .where(TradingHaltORM.cleared_at.is_(None))
            .order_by(TradingHaltORM.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_recent_cleared(self, limit: int = 10) -> list[TradingHaltORM]:
        stmt = (
            select(TradingHaltORM)
            .where(TradingHaltORM.cleared_at.is_not(None))
            .order_by(TradingHaltORM.cleared_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_if_absent(
        self,
        *,
        scope: HaltScope,
        symbol: str | None,
        source: HaltSource,
        rule: str,
        reason: str,
        created_by: str,
    ) -> TradingHaltORM | None:
        """Cria a trava, salvo se já houver uma ativa com o mesmo escopo,
        símbolo e regra (halt automático não se duplica a cada sinal).
        Retorna a trava criada, ou None se já existia."""
        existing = await self.session.execute(
            select(TradingHaltORM.id).where(
                TradingHaltORM.cleared_at.is_(None),
                TradingHaltORM.scope == scope.value,
                TradingHaltORM.symbol.is_(None)
                if symbol is None
                else TradingHaltORM.symbol == symbol,
                TradingHaltORM.rule == rule,
            )
        )
        if existing.first() is not None:
            return None
        return await self.add(
            TradingHaltORM(
                scope=scope.value,
                symbol=symbol,
                source=source.value,
                rule=rule,
                reason=reason,
                created_by=created_by,
            )
        )

    async def clear(
        self,
        *,
        scope: HaltScope,
        symbol: str | None,
        cleared_by: str,
        note: str | None = None,
    ) -> int:
        """Libera todas as travas ativas do escopo. Retorna quantas."""
        stmt = (
            update(TradingHaltORM)
            .where(
                TradingHaltORM.cleared_at.is_(None),
                TradingHaltORM.scope == scope.value,
                TradingHaltORM.symbol.is_(None)
                if symbol is None
                else TradingHaltORM.symbol == symbol,
            )
            .values(
                cleared_at=datetime.now(UTC),
                cleared_by=cleared_by,
                clear_note=note,
                updated_at=func.now(),
            )
        )
        result = cast(CursorResult, await self.session.execute(stmt))
        await self.session.flush()
        return result.rowcount or 0
