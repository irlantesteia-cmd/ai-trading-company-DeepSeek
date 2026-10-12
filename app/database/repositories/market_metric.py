from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database.models.market_metric import MarketMetricORM
from app.database.repositories.base import BaseRepository

METRIC_COLUMNS = (
    "open_interest",
    "open_interest_value",
    "global_ls_ratio",
    "top_ls_position_ratio",
    "taker_buy_sell_ratio",
    "taker_buy_volume",
    "taker_sell_volume",
    "mark_price",
    "index_price",
    "funding_rate",
    "book_bid_notional",
    "book_ask_notional",
    "book_imbalance",
    "spread_bps",
)


class MarketMetricRepository(BaseRepository[MarketMetricORM]):
    model = MarketMetricORM

    async def bulk_upsert(self, rows: list[dict]) -> int:
        """Insere ou completa linhas (symbol, ts). No conflito, cada coluna só
        é sobrescrita por um valor não nulo: fontes diferentes preenchem a
        mesma linha sem apagar umas às outras. As linhas precisam ter as
        mesmas chaves e não repetir (symbol, ts) no mesmo lote."""
        if not rows:
            return 0
        stmt = pg_insert(MarketMetricORM).values(rows)
        table = MarketMetricORM.__table__.c
        stmt = stmt.on_conflict_do_update(
            constraint="uq_market_metrics_symbol_ts",
            set_={
                **{
                    col: func.coalesce(getattr(stmt.excluded, col), table[col])
                    for col in METRIC_COLUMNS
                },
                "updated_at": func.now(),
            },
        )
        result = cast(CursorResult, await self.session.execute(stmt))
        await self.session.flush()
        return result.rowcount or 0

    async def last_history_ts(self, symbol: str) -> datetime | None:
        """Último bloco com dado histórico (open interest) gravado."""
        stmt = select(func.max(MarketMetricORM.ts)).where(
            MarketMetricORM.symbol == symbol,
            MarketMetricORM.open_interest.is_not(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
