r"""Coletor de métricas de mercado (L5b): roda separado do bot e só lê dados
públicos da Binance (sem chave, sem ordens).

Uso (PowerShell):
    docker compose up -d collector                  # em container (recomendado)
    .\.venv\Scripts\python scripts/run_collector.py  # local
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.core.config import settings
from app.core.logging import setup_logging
from app.database.repositories.market_metric import METRIC_COLUMNS
from app.database.session import AsyncSessionLocal
from app.market.collector import PERIOD_MS, DbMetricStore, MarketDataCollector

logger = logging.getLogger(__name__)
# Coleta alguns segundos depois da virada do bloco de 5 min, quando a Binance
# já publicou o bloco anterior.
_OFFSET_S = 20.0


def _sleep_until_next_cycle(interval_s: float) -> float:
    period = interval_s if interval_s > 0 else PERIOD_MS / 1000
    return period - (time.time() % period) + _OFFSET_S


async def main() -> None:
    setup_logging(settings.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # uma linha por requisição é ruído
    async with httpx.AsyncClient(base_url=settings.collector_base_url, timeout=20.0) as client:
        collector = MarketDataCollector(
            client=client,
            store=DbMetricStore(AsyncSessionLocal),
            symbols=list(settings.collector_symbols),
            columns=METRIC_COLUMNS,
            backfill_days=settings.collector_backfill_days,
        )
        logger.info("collector.starting", extra={"symbols": settings.collector_symbols})
        await collector.backfill()
        while True:
            await asyncio.sleep(_sleep_until_next_cycle(settings.collector_interval_s))
            try:
                await collector.collect_once()
            except Exception:
                logger.exception("collector.cycle_failed")


if __name__ == "__main__":
    asyncio.run(main())
