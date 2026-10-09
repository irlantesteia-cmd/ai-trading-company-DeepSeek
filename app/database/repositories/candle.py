from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.enums import MarketType
from app.database.models.candle import CandleORM
from app.database.repositories.base import BaseRepository


class CandleRepository(BaseRepository[CandleORM]):
    model = CandleORM

    async def bulk_upsert(self, rows: list[dict]) -> int:
        """Upsert em lote: insere ou, no conflito, atualiza OHLCV e taker_buy.

        Retorna o rowcount do statement (inserts + updates). Idempotente
        para a chave única. Reingerir re-preenche `taker_buy_base_volume`
        em candles anteriores à migration 0004 e corrige candles que tenham
        sido gravados parciais num backfill anterior.
        """
        if not rows:
            return 0
        stmt = pg_insert(CandleORM).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle_symbol_market_interval_time",
            set_={
                "close_time": stmt.excluded.close_time,
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "trades": stmt.excluded.trades,
                "taker_buy_base_volume": stmt.excluded.taker_buy_base_volume,
            },
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