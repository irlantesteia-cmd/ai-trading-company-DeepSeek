"""Regrava candles persistidos ainda em andamento (OHLCV parcial).

Até a correção do `map_kline`, o backfill gravava a barra em andamento (último
item do REST) com `closed=True`. Com `ON CONFLICT DO NOTHING` (pré-ML-3d-2),
essas linhas nunca foram corrigidas.

Fluxo:
1. Seleciona candles com `created_at < close_time` e `updated_at < close_time`.
2. Busca cada um na Binance (REST público, mesmo ambiente do backfill:
   demo se `BINANCE_TESTNET=true`) por `open_time`.
3. Dry-run (default): mostra o diff close/volume. `--apply`: grava via
   `CandleRepository.bulk_upsert`.

Idempotente: depois do `--apply`, `updated_at > close_time` e a linha sai da
seleção.

Uso (PowerShell):
    python scripts/repair_open_candles.py
    python scripts/repair_open_candles.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.enums import MarketType
from app.core.logging import setup_logging
from app.database.models.candle import CandleORM
from app.database.repositories.candle import CandleRepository
from app.database.session import AsyncSessionLocal
from app.domain.models.market import Candle
from app.exchanges.binance.mappers import map_kline

logger = logging.getLogger("repair_open_candles")

_HTTP_TIMEOUT_S = 30.0
_BASE_URLS = {
    # (market_type, testnet) → (base_url, path)
    (MarketType.FUTURES, True): ("https://demo-fapi.binance.com", "/fapi/v1/klines"),
    (MarketType.FUTURES, False): ("https://fapi.binance.com", "/fapi/v1/klines"),
    (MarketType.SPOT, True): ("https://demo-api.binance.com", "/api/v3/klines"),
    (MarketType.SPOT, False): ("https://api.binance.com", "/api/v3/klines"),
}


def _to_ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)


async def fetch_closed_candle(
    client: httpx.AsyncClient,
    row: CandleORM,
    *,
    testnet: bool,
    now: datetime | None = None,
) -> Candle | None:
    """Busca o candle de `row.open_time`. None se a API não tiver o candle
    exato ou se ele ainda estiver em andamento."""
    market_type = MarketType(row.market_type)
    base_url, path = _BASE_URLS[(market_type, testnet)]
    open_ms = _to_ms(row.open_time)
    r = await client.get(
        f"{base_url}{path}",
        params={
            "symbol": row.symbol,
            "interval": row.interval,
            "startTime": open_ms,
            "endTime": open_ms,
            "limit": 1,
        },
    )
    r.raise_for_status()
    raw = r.json()
    if not raw or int(raw[0][0]) != open_ms:
        return None
    candle = map_kline(raw[0], market_type, row.symbol, row.interval, now=now)
    return candle if candle.closed else None


def to_row(c: Candle) -> dict:
    return {
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


def _pct(new, old) -> str:
    if not old:
        return "n/a"
    return f"{(float(new) / float(old) - 1) * 100:+.1f}%"


async def main(apply: bool) -> None:
    setup_logging("INFO")
    testnet = settings.binance_testnet

    async with AsyncSessionLocal() as session:
        stale = await CandleRepository(session).list_persisted_while_open()

    if not stale:
        print("Nenhum candle parcial. Nada a fazer.")
        return

    print(
        f"{len(stale)} candles gravados em andamento "
        f"(fonte: {'demo' if testnet else 'real'}). Buscando na exchange..."
    )
    fixed: list[Candle] = []
    missing = 0
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        for row in stale:
            candle = await fetch_closed_candle(client, row, testnet=testnet)
            if candle is None:
                missing += 1
                logger.warning(
                    "repair.candle_unavailable",
                    extra={"symbol": row.symbol, "open_time": row.open_time.isoformat()},
                )
                continue
            fixed.append(candle)
            print(
                f"  {row.symbol:10s} {row.open_time:%Y-%m-%d %H:%M}  "
                f"close {row.close} → {candle.close}  "
                f"volume {row.volume} → {candle.volume} ({_pct(candle.volume, row.volume)})"
            )

    print()
    print(f"Corrigíveis: {len(fixed)}  sem dado na API: {missing}")
    if not apply:
        print("Dry-run: nada foi gravado. Rode com --apply para gravar.")
        return

    async with AsyncSessionLocal() as session:
        written = await CandleRepository(session).bulk_upsert([to_row(c) for c in fixed])
        await session.commit()
    print(f"Gravados: {written}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Regrava candles persistidos em andamento.")
    p.add_argument(
        "--apply",
        action="store_true",
        help="grava as correções (default: dry-run, só mostra o diff)",
    )
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(apply=_parse_args().apply))
