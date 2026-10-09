"""Serviço de backfill de candles históricos.

Busca candles da exchange via `MarketDataProvider.get_candles` e persiste
em lote no DB. Idempotente: re-execução não duplica (chave única + ON CONFLICT).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import MarketType
from app.database.models.candle import CandleORM
from app.database.repositories.candle import CandleRepository
from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillReport:
    symbols_processed: int
    candles_fetched: int
    candles_persisted: int

    @property
    def duplicates_skipped(self) -> int:
        return self.candles_fetched - self.candles_persisted


class HistoryBackfillService:
    """Orquestra backfill de candles para uma lista de símbolos."""

    def __init__(
        self,
        *,
        exchange: Exchange,
        session_factory: async_sessionmaker,
    ) -> None:
        self._exchange = exchange
        self._session_factory = session_factory

    async def backfill(
        self,
        *,
        symbols: list[str],
        interval: str,
        market_type: MarketType,
        limit: int = 500,
    ) -> BackfillReport:
        total_fetched = 0
        total_persisted = 0
        processed = 0

        for symbol in symbols:
            try:
                report = await self._backfill_one(
                    symbol=symbol,
                    interval=interval,
                    market_type=market_type,
                    limit=limit,
                )
            except Exception:
                logger.exception(
                    "backfill.symbol_failed",
                    extra={"symbol": symbol, "interval": interval},
                )
                continue

            processed += 1
            total_fetched += report["fetched"]
            total_persisted += report["persisted"]

        report = BackfillReport(
            symbols_processed=processed,
            candles_fetched=total_fetched,
            candles_persisted=total_persisted,
        )
        logger.info(
            "backfill.completed",
            extra={
                "symbols_processed": report.symbols_processed,
                "candles_fetched": report.candles_fetched,
                "candles_persisted": report.candles_persisted,
                "duplicates_skipped": report.duplicates_skipped,
            },
        )
        return report

    async def _backfill_one(
        self,
        *,
        symbol: str,
        interval: str,
        market_type: MarketType,
        limit: int,
    ) -> dict[str, int]:
        candles = await self._exchange.market_data.get_candles(
            symbol=symbol,
            interval=interval,
            market_type=market_type,
            limit=limit,
        )
        # A barra em andamento (último item do REST) tem OHLCV parcial.
        candles = [c for c in candles if c.closed]
        if not candles:
            return {"fetched": 0, "persisted": 0}

        rows = [
            {
                "symbol": c.symbol,
                "market_type": c.market_type.value,
                "interval": c.interval,
                "open_time": c.open_time,
                "close_time": c.close_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "trades": c.trades,
                "taker_buy_base_volume": c.taker_buy_base_volume,
            }
            for c in candles
        ]

        async with self._session_factory() as session:
            repo = CandleRepository(session)
            persisted = await repo.bulk_upsert(rows)
            await session.commit()

        logger.info(
            "backfill.symbol_done",
            extra={
                "symbol": symbol,
                "interval": interval,
                "fetched": len(candles),
                "persisted": persisted,
                "duplicates": len(candles) - persisted,
            },
        )
        return {"fetched": len(candles), "persisted": persisted}


__all__ = ["BackfillReport", "CandleORM", "HistoryBackfillService"]