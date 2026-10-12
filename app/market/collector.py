"""Coletor de métricas de derivativos (L5b): dados que a Binance só guarda por
~30 dias, ou nem guarda, para pesquisar estratégias que hoje não dá para testar.

A cada bloco de 5 min, por ativo:
- histórico de 5 min (`/futures/data/*`): open interest, razão long/short das
  contas, razão long/short das posições dos maiores traders, volume taker;
- fotos do momento: preço de marcação/índice e funding (`premiumIndex`) e as
  20 melhores ofertas de cada lado do livro (`depth`): notional, desequilíbrio
  e spread.

No início, completa o histórico desde o último bloco gravado (até ~30 dias).
Só lê dados públicos (sem chave, sem ordens) e é independente do bot.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories.market_metric import MarketMetricRepository

logger = logging.getLogger(__name__)

PERIOD = "5m"
PERIOD_MS = 5 * 60_000
_HIST_PAGE = 500  # máximo por chamada nos endpoints /futures/data
BOOK_LEVELS = 20

Row = dict[str, Any]
# Falhas esperadas de uma fonte: rede/HTTP ou payload inesperado.
_FETCH_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError)


def bucket(dt: datetime) -> datetime:
    """Início do bloco de 5 min que contém `dt` (UTC)."""
    ms = int(dt.timestamp() * 1000)
    return datetime.fromtimestamp((ms - ms % PERIOD_MS) / 1000, tz=UTC)


def _ts(ms: int | str) -> datetime:
    return bucket(datetime.fromtimestamp(int(ms) / 1000, tz=UTC))


def _dec(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


# ------------------------------------------------------------------ parsers
def parse_open_interest(symbol: str, data: list[dict]) -> list[Row]:
    return [
        {
            "symbol": symbol,
            "ts": _ts(r["timestamp"]),
            "open_interest": _dec(r.get("sumOpenInterest")),
            "open_interest_value": _dec(r.get("sumOpenInterestValue")),
        }
        for r in data
    ]


def parse_global_ratio(symbol: str, data: list[dict]) -> list[Row]:
    return [
        {"symbol": symbol, "ts": _ts(r["timestamp"]), "global_ls_ratio": _dec(r.get("longShortRatio"))}
        for r in data
    ]


def parse_top_position_ratio(symbol: str, data: list[dict]) -> list[Row]:
    return [
        {
            "symbol": symbol,
            "ts": _ts(r["timestamp"]),
            "top_ls_position_ratio": _dec(r.get("longShortRatio")),
        }
        for r in data
    ]


def parse_taker_ratio(symbol: str, data: list[dict]) -> list[Row]:
    return [
        {
            "symbol": symbol,
            "ts": _ts(r["timestamp"]),
            "taker_buy_sell_ratio": _dec(r.get("buySellRatio")),
            "taker_buy_volume": _dec(r.get("buyVol")),
            "taker_sell_volume": _dec(r.get("sellVol")),
        }
        for r in data
    ]


def parse_premium(symbol: str, data: dict, now: datetime) -> Row:
    return {
        "symbol": symbol,
        "ts": bucket(now),
        "mark_price": _dec(data.get("markPrice")),
        "index_price": _dec(data.get("indexPrice")),
        "funding_rate": _dec(data.get("lastFundingRate")),
    }


def parse_book(symbol: str, data: dict, now: datetime, levels: int = BOOK_LEVELS) -> Row | None:
    """Notional das `levels` melhores ofertas de cada lado; desequilíbrio =
    (compra - venda) / (compra + venda), de -1 a +1; spread em bps do meio."""
    bids = [(Decimal(p), Decimal(q)) for p, q in data.get("bids", [])[:levels]]
    asks = [(Decimal(p), Decimal(q)) for p, q in data.get("asks", [])[:levels]]
    if not bids or not asks:
        return None
    bid_notional = sum((p * q for p, q in bids), Decimal(0))
    ask_notional = sum((p * q for p, q in asks), Decimal(0))
    total = bid_notional + ask_notional
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    return {
        "symbol": symbol,
        "ts": bucket(now),
        "book_bid_notional": bid_notional,
        "book_ask_notional": ask_notional,
        "book_imbalance": (bid_notional - ask_notional) / total if total > 0 else None,
        "spread_bps": (best_ask - best_bid) / mid * 10_000 if mid > 0 else None,
    }


HISTORY_ENDPOINTS: dict[str, Callable[[str, list[dict]], list[Row]]] = {
    "/futures/data/openInterestHist": parse_open_interest,
    "/futures/data/globalLongShortAccountRatio": parse_global_ratio,
    "/futures/data/topLongShortPositionRatio": parse_top_position_ratio,
    "/futures/data/takerlongshortRatio": parse_taker_ratio,
}


def merge_rows(rows: Iterable[Row], columns: Iterable[str]) -> list[Row]:
    """Junta as linhas de várias fontes em uma por (symbol, ts), com todas as
    colunas (None onde a fonte não informou): o upsert em lote exige chaves
    iguais e não aceita a mesma linha duas vezes."""
    cols = tuple(columns)
    merged: dict[tuple[str, datetime], Row] = {}
    for row in rows:
        key = (row["symbol"], row["ts"])
        target = merged.setdefault(key, {"symbol": key[0], "ts": key[1], **dict.fromkeys(cols)})
        for col, value in row.items():
            if col in cols and value is not None:
                target[col] = value
    return sorted(merged.values(), key=lambda r: (r["symbol"], r["ts"]))


class MetricStore(Protocol):
    async def upsert(self, rows: list[Row]) -> None: ...

    async def last_history_ts(self, symbol: str) -> datetime | None: ...


class MarketDataCollector:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        store: MetricStore,
        symbols: list[str],
        columns: Iterable[str],
        backfill_days: float = 29.5,
        pause_s: float = 0.3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._symbols = symbols
        self._columns = tuple(columns)
        self._backfill = timedelta(days=backfill_days)
        self._pause_s = pause_s
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _get(self, path: str, params: dict) -> Any:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        if self._pause_s:
            await asyncio.sleep(self._pause_s)  # gentil com o limite de requisições
        return response.json()

    async def _save(self, rows: list[Row]) -> int:
        merged = merge_rows(rows, self._columns)
        if merged:
            await self._store.upsert(merged)
        return len(merged)

    async def backfill(self) -> int:
        """Completa o histórico de 5 min de cada ativo desde o último bloco
        gravado (ou ~30 dias). Falha num ativo não para os outros."""
        now = self._clock()
        total = 0
        for symbol in self._symbols:
            try:
                last = await self._store.last_history_ts(symbol)
                start = max(now - self._backfill, last) if last else now - self._backfill
                total += await self._backfill_symbol(symbol, start, now)
            except Exception:
                logger.exception("collector.backfill_failed", extra={"symbol": symbol})
        logger.info("collector.backfill_done", extra={"rows": total})
        return total

    async def _backfill_symbol(self, symbol: str, start: datetime, end: datetime) -> int:
        rows: list[Row] = []
        window = PERIOD_MS * _HIST_PAGE
        for path, parse in HISTORY_ENDPOINTS.items():
            lo = int(start.timestamp() * 1000)
            hi_end = int(end.timestamp() * 1000)
            while lo < hi_end:
                hi = min(lo + window - 1, hi_end)
                data = await self._get(
                    path,
                    {"symbol": symbol, "period": PERIOD, "limit": _HIST_PAGE,
                     "startTime": lo, "endTime": hi},
                )
                rows.extend(parse(symbol, data))
                lo = hi + 1
        return await self._save(rows)

    async def collect_once(self) -> int:
        """Um ciclo: os 2 blocos mais recentes de cada histórico (o último
        pode chegar atrasado) e as fotos do momento. Falha numa fonte só
        perde aquela fonte naquele ciclo."""
        now = self._clock()
        rows: list[Row] = []
        for symbol in self._symbols:
            for path, parse in HISTORY_ENDPOINTS.items():
                try:
                    data = await self._get(path, {"symbol": symbol, "period": PERIOD, "limit": 2})
                    rows.extend(parse(symbol, data))
                except _FETCH_ERRORS as exc:
                    logger.warning("collector.fetch_failed",
                                   extra={"symbol": symbol, "path": path, "error": repr(exc)})
            try:
                premium = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
                rows.append(parse_premium(symbol, premium, now))
                book = parse_book(
                    symbol, await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": BOOK_LEVELS}), now
                )
                if book is not None:
                    rows.append(book)
            except _FETCH_ERRORS as exc:
                logger.warning("collector.snapshot_failed", extra={"symbol": symbol, "error": repr(exc)})
        saved = await self._save(rows)
        logger.info("collector.cycle_done", extra={"rows": saved, "symbols": len(self._symbols)})
        return saved


class DbMetricStore:
    """`MetricStore` no Postgres (`market_metrics`), em lotes: o asyncpg aceita
    no máximo 32.767 parâmetros por comando."""

    BATCH = 1000

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def upsert(self, rows: list[Row]) -> None:
        async with self._session_factory() as session:
            repo = MarketMetricRepository(session)
            for i in range(0, len(rows), self.BATCH):
                await repo.bulk_upsert(rows[i : i + self.BATCH])
            await session.commit()

    async def last_history_ts(self, symbol: str) -> datetime | None:
        async with self._session_factory() as session:
            return await MarketMetricRepository(session).last_history_ts(symbol)
