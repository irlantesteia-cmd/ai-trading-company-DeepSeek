from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.enums import MarketType
from app.database.models.candle import CandleORM
from app.database.repositories.base import BaseRepository


class CandleRepository(BaseRepository[CandleORM]):
    model = CandleORM

    async def bulk_insert_ignore_conflicts(
        self, rows: list[dict]
    ) -> int:
        """Insere em lote, ignorando conflitos de chave única.

        Retorna o número de linhas efetivamente inseridas (rowcount).
        Idempotente: chamar 2× com o mesmo conjunto não duplica.
        """
        if not rows:
            return 0
        stmt = (
            pg_insert(CandleORM)
            .values(rows)
            .on_conflict_do_nothing(
                constraint="uq_candle_symbol_market_interval_time"
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount or 0

    async def get_recent(
        self,
        *,
        symbol: str,
        market_type: MarketType,
        interval: str,
        limit: int = 500,
    ) -> list[CandleORM]:
        """Retorna os `limit` candles mais recentes em ordem cronológica (asc)."""
        stmt = (
            select(CandleORM)
            .where(
                CandleORM.symbol == symbol,
                CandleORM.market_type == market_type.value,
                CandleORM.interval == interval,
            )
            .order_by(CandleORM.open_time.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        rows.reverse()
        return rows

    async def count_for(
        self,
        *,
        symbol: str,
        market_type: MarketType,
        interval: str,
    ) -> int:
        stmt = select(CandleORM.id).where(
            CandleORM.symbol == symbol,
            CandleORM.market_type == market_type.value,
            CandleORM.interval == interval,
        )
        return len((await self.session.execute(stmt)).scalars().all())