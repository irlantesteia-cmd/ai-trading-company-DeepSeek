"""L5b: coletor de métricas de derivativos (open interest, long/short, taker,
funding, livro de ofertas)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.database.repositories.market_metric import METRIC_COLUMNS, MarketMetricRepository
from app.market.collector import (
    HISTORY_ENDPOINTS,
    PERIOD_MS,
    DbMetricStore,
    MarketDataCollector,
    bucket,
    merge_rows,
    parse_book,
    parse_open_interest,
    parse_premium,
    parse_taker_ratio,
)

NOW = datetime(2026, 10, 12, 3, 7, 41, tzinfo=UTC)
T = datetime(2026, 10, 12, 3, 5, tzinfo=UTC)
T_MS = int(T.timestamp() * 1000)


def test_bucket_floors_to_5_minutes():
    assert bucket(NOW) == T
    assert bucket(T) == T


def test_parsers_map_binance_payloads():
    (oi,) = parse_open_interest("BTCUSDT", [
        {"symbol": "BTCUSDT", "sumOpenInterest": "81234.5", "sumOpenInterestValue": "5.1e9",
         "timestamp": T_MS},
    ])
    assert oi["ts"] == T and oi["open_interest"] == Decimal("81234.5")
    (tk,) = parse_taker_ratio("BTCUSDT", [
        {"buySellRatio": "1.25", "buyVol": "500", "sellVol": "400", "timestamp": str(T_MS)},
    ])
    assert tk["taker_buy_sell_ratio"] == Decimal("1.25") and tk["taker_sell_volume"] == Decimal(400)
    pr = parse_premium("BTCUSDT", {"markPrice": "62000.1", "indexPrice": "61990",
                                   "lastFundingRate": "0.0001"}, NOW)
    assert pr["ts"] == T and pr["funding_rate"] == Decimal("0.0001")


def test_book_imbalance_and_spread():
    row = parse_book("BTCUSDT", {"bids": [["99", "3"], ["98", "1"]],
                                 "asks": [["101", "1"], ["102", "1"]]}, NOW)
    assert row is not None
    assert row["book_bid_notional"] == Decimal(395)   # 99×3 + 98
    assert row["book_ask_notional"] == Decimal(203)   # 101 + 102
    assert row["book_imbalance"] == pytest.approx(Decimal(192) / Decimal(598))
    assert row["spread_bps"] == Decimal(200)          # (101 - 99) / 100 × 10.000
    assert parse_book("BTCUSDT", {"bids": [], "asks": [["1", "1"]]}, NOW) is None


def test_merge_rows_joins_sources_into_one_row_per_block():
    rows = [
        {"symbol": "A", "ts": T, "open_interest": Decimal(1), "open_interest_value": None},
        {"symbol": "A", "ts": T, "funding_rate": Decimal("0.0001")},
        {"symbol": "B", "ts": T, "funding_rate": Decimal(0)},
    ]
    merged = merge_rows(rows, METRIC_COLUMNS)
    assert [(r["symbol"], r["ts"]) for r in merged] == [("A", T), ("B", T)]
    assert merged[0]["open_interest"] == 1 and merged[0]["funding_rate"] == Decimal("0.0001")
    assert all(set(r) == {"symbol", "ts", *METRIC_COLUMNS} for r in merged)
    assert merged[1]["funding_rate"] == 0  # zero não é tratado como ausente


# ---------------------------------------------------------------- coletor
class _Store:
    def __init__(self, last: dict[str, datetime] | None = None) -> None:
        self.rows: list[dict] = []
        self.last = last or {}

    async def upsert(self, rows):
        self.rows.extend(rows)

    async def last_history_ts(self, symbol):
        return self.last.get(symbol)


def _handler(calls: list[httpx.Request], *, fail: set[str] = frozenset()):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path in fail:
            return httpx.Response(500, json={"msg": "boom"})
        if path == "/fapi/v1/premiumIndex":
            return httpx.Response(200, json={"markPrice": "100", "indexPrice": "100",
                                             "lastFundingRate": "0.0002"})
        if path == "/fapi/v1/depth":
            return httpx.Response(200, json={"bids": [["99", "1"]], "asks": [["101", "1"]]})
        point = {"timestamp": T_MS, "sumOpenInterest": "10", "sumOpenInterestValue": "1000",
                 "longShortRatio": "1.5", "buySellRatio": "0.9", "buyVol": "1", "sellVol": "2"}
        return httpx.Response(200, json=[point])
    return handle


def _collector(store, calls, **kw) -> tuple[MarketDataCollector, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls, **kw)),
                               base_url="https://fapi.binance.com")
    collector = MarketDataCollector(client=client, store=store, symbols=["BTCUSDT", "ETHUSDT"],
                                    columns=METRIC_COLUMNS, pause_s=0, clock=lambda: NOW)
    return collector, client


async def test_collect_once_saves_history_and_snapshots_per_symbol():
    store, calls = _Store(), []
    collector, client = _collector(store, calls)
    async with client:
        saved = await collector.collect_once()
    assert saved == 2 and len(store.rows) == 2   # 1 bloco × 2 ativos (tudo no mesmo bloco T)
    btc = next(r for r in store.rows if r["symbol"] == "BTCUSDT")
    assert btc["open_interest"] == 10 and btc["global_ls_ratio"] == Decimal("1.5")
    assert btc["funding_rate"] == Decimal("0.0002") and btc["spread_bps"] == Decimal(200)
    assert len(calls) == 2 * (len(HISTORY_ENDPOINTS) + 2)


async def test_one_failing_source_does_not_lose_the_others():
    store, calls = _Store(), []
    collector, client = _collector(store, calls, fail={"/futures/data/openInterestHist"})
    async with client:
        await collector.collect_once()
    btc = next(r for r in store.rows if r["symbol"] == "BTCUSDT")
    assert btc["open_interest"] is None and btc["taker_buy_sell_ratio"] == Decimal("0.9")


async def test_backfill_pages_30_days_or_resumes_from_last_block():
    store, calls = _Store(last={"ETHUSDT": NOW - timedelta(hours=3)}), []
    collector, client = _collector(store, calls)
    async with client:
        await collector.backfill()

    def windows(symbol: str) -> list[tuple[int, int]]:
        return [(int(c.url.params["startTime"]), int(c.url.params["endTime"])) for c in calls
                if c.url.params["symbol"] == symbol
                and c.url.path == "/futures/data/openInterestHist"]

    btc, eth = windows("BTCUSDT"), windows("ETHUSDT")
    page = 500 * PERIOD_MS
    assert len(btc) == -(-int(timedelta(days=29.5).total_seconds() * 1000) // page)  # teto
    assert all(b[0] == a[1] + 1 for a, b in pairwise(btc))  # janelas contíguas
    assert eth == [(int((NOW - timedelta(hours=3)).timestamp() * 1000), int(NOW.timestamp() * 1000))]


async def test_backfill_failure_in_one_symbol_keeps_going():
    store, calls = _Store(), []
    collector, client = _collector(store, calls, fail={"/futures/data/takerlongshortRatio"})
    async with client:
        await collector.backfill()
    assert {c.url.params["symbol"] for c in calls} == {"BTCUSDT", "ETHUSDT"}


# ------------------------------------------------------------------ banco
class _CaptureSession:
    def __init__(self) -> None:
        self.sql = ""

    async def execute(self, stmt):
        self.sql = str(stmt.compile(dialect=postgresql.dialect()))
        raise RuntimeError("só a SQL interessa")


async def test_upsert_only_overwrites_with_non_null_values():
    session = _CaptureSession()
    row = {"symbol": "A", "ts": T, **dict.fromkeys(METRIC_COLUMNS)}
    with pytest.raises(RuntimeError):
        await MarketMetricRepository(session).bulk_upsert([row])  # type: ignore[arg-type]
    sql = session.sql
    assert "ON CONFLICT ON CONSTRAINT uq_market_metrics_symbol_ts DO UPDATE" in sql
    assert "coalesce(excluded.funding_rate, market_metrics.funding_rate)" in sql


async def test_db_store_upserts_in_batches(monkeypatch):
    batches: list[int] = []

    async def bulk_upsert(_self, rows):
        batches.append(len(rows))
        return len(rows)

    monkeypatch.setattr(MarketMetricRepository, "bulk_upsert", bulk_upsert)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            pass

    await DbMetricStore(_Session).upsert([{"symbol": "A", "ts": T}] * 2500)  # type: ignore[arg-type]
    assert batches == [1000, 1000, 500]
