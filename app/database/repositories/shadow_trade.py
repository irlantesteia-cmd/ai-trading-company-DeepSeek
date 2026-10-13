from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database.models.shadow_trade import ShadowTradeORM
from app.database.repositories.base import BaseRepository

KEY_COLUMNS = ("hypothesis", "symbol", "interval", "entry_time")


class ShadowTradeRepository(BaseRepository[ShadowTradeORM]):
    model = ShadowTradeORM

    async def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """Insere ou atualiza pela chave (hipótese, ativo, intervalo, entrada):
        a simulação refeita é a fonte da verdade, então todas as outras colunas
        são sobrescritas (posição aberta → fechada, stop móvel, marcação)."""
        if not rows:
            return 0
        stmt = pg_insert(ShadowTradeORM).values(rows)
        updatable = [k for k in rows[0] if k not in KEY_COLUMNS]
        stmt = stmt.on_conflict_do_update(
            constraint="uq_shadow_trades_key",
            set_={**{col: getattr(stmt.excluded, col) for col in updatable}, "updated_at": func.now()},
        )
        result = cast(CursorResult, await self.session.execute(stmt))
        await self.session.flush()
        return result.rowcount or 0

    async def list_for(self, hypothesis: str) -> Sequence[ShadowTradeORM]:
        stmt = (
            select(ShadowTradeORM)
            .where(ShadowTradeORM.hypothesis == hypothesis)
            .order_by(ShadowTradeORM.entry_time, ShadowTradeORM.symbol, ShadowTradeORM.interval)
        )
        return (await self.session.execute(stmt)).scalars().all()
