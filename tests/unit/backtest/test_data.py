"""Carregador de klines do laboratório: paginação, cache e candle aberto."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.backtest.data import KlineCache, interval_ms, load_candles

STEP = 5 * 60_000
NOW = datetime(2026, 10, 10, 12, 2, tzinfo=UTC)  # barra 12:00 ainda aberta
NOW_MS = int(NOW.timestamp() * 1000)


def _row(open_ms: int) -> list:
    return [open_ms, "1", "2", "0.5", "1.5", "10", open_ms + STEP - 1, "0", 3, "4", "0", "0"]


def _exchange(first_open_ms: int, requests: list):
    """Exchange falsa com candles de 5m de `first_open_ms` até a barra aberta de NOW."""
    last_open = (NOW_MS // STEP) * STEP
    first = (first_open_ms // STEP) * STEP  # grade de 5m, como a Binance
    rows = [_row(t) for t in range(first, last_open + 1, STEP)]

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        requests.append(dict(q))
        limit = int(q["limit"])
        if "startTime" in q:
            start, end = int(q["startTime"]), int(q.get("endTime", 10**15))
            page = [r for r in rows if start <= r[0] <= end][:limit]
        else:
            end = int(q["endTime"])
            page = [r for r in rows if r[0] <= end][-limit:]
        return httpx.Response(200, json=page)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_interval_ms():
    assert interval_ms("5m") == STEP
    assert interval_ms("1h") == 3_600_000
    with pytest.raises(ValueError):
        interval_ms("7x")


async def test_first_load_fetches_and_never_caches_the_open_candle(tmp_path):
    cache = KlineCache(tmp_path)
    requests: list = []
    start = NOW_MS - 2 * 86_400_000
    async with _exchange(start - 10 * STEP, requests) as client:
        candles = await load_candles("BTCUSDT", "5m", days=1, now=NOW, client=client, cache=cache)

    assert all(c.closed for c in candles)
    assert candles[-1].open_time == datetime(2026, 10, 10, 11, 55, tzinfo=UTC)
    assert candles[0].open_time >= NOW - timedelta(days=1)
    cached = cache.read("BTCUSDT", "5m")
    assert max(int(r[0]) for r in cached) == candles[-1].open_time.timestamp() * 1000  # sem a barra 12:00


async def test_second_load_uses_cache_without_http(tmp_path):
    cache = KlineCache(tmp_path)
    async with _exchange(NOW_MS - 3 * 86_400_000, []) as client:
        first = await load_candles("BTCUSDT", "5m", days=1, now=NOW, client=client, cache=cache)

    requests: list = []
    async with _exchange(NOW_MS - 3 * 86_400_000, requests) as client:
        again = await load_candles("BTCUSDT", "5m", days=1, now=NOW, client=client, cache=cache)

    assert requests == []
    assert [c.open_time for c in again] == [c.open_time for c in first]


async def test_later_load_fetches_only_new_candles(tmp_path):
    cache = KlineCache(tmp_path)
    async with _exchange(NOW_MS - 3 * 86_400_000, []) as client:
        await load_candles("BTCUSDT", "5m", days=1, now=NOW, client=client, cache=cache)

    later = NOW + timedelta(minutes=30)
    requests: list = []
    async with _exchange(NOW_MS - 3 * 86_400_000, requests) as client:
        # a exchange falsa só conhece até NOW; o pedido mostra a busca incremental
        await load_candles("BTCUSDT", "5m", days=1, now=later, client=client, cache=cache)

    assert len(requests) == 1 and "startTime" in requests[0]


async def test_longer_period_fetches_only_older_candles(tmp_path):
    cache = KlineCache(tmp_path)
    span = 12 * 86_400_000
    async with _exchange(NOW_MS - span, []) as client:
        # 1 dia pedido; a 1ª página (1.500 candles ≈ 5,2 dias) fica no cache
        await load_candles("BTCUSDT", "5m", days=1, now=NOW, client=client, cache=cache)

    requests: list = []
    async with _exchange(NOW_MS - span, requests) as client:
        candles = await load_candles("BTCUSDT", "5m", days=8, now=NOW, client=client, cache=cache)

    assert requests and all("endTime" in r and "startTime" not in r for r in requests)
    assert candles[0].open_time <= NOW - timedelta(days=8) + timedelta(minutes=5)
    times = [c.open_time for c in candles]
    assert times == sorted(set(times))  # sem duplicatas e em ordem


# ------------------------------------------- vários laboratórios em paralelo
def test_cache_write_uses_a_private_temp_file_and_cleans_up(tmp_path, monkeypatch):
    from pathlib import Path

    cache = KlineCache(tmp_path)
    temps: list[str] = []
    real_replace = Path.replace

    def spy(self, target):
        temps.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    assert cache.write("BTCUSDT", "15m", [[1]])
    assert cache.write("BTCUSDT", "15m", [[2]])
    assert len(set(temps)) == 2  # um temporário por gravação
    assert cache.read("BTCUSDT", "15m") == [[2]]
    assert [p.name for p in tmp_path.iterdir()] == ["BTCUSDT_15m.json.gz"]


def test_cache_write_retries_then_gives_up_without_raising(tmp_path, monkeypatch, caplog):
    """No Windows a troca falha enquanto outro processo usa o arquivo
    (visto ao rodar 3 laboratórios juntos em 2026-10-12)."""
    from pathlib import Path

    cache = KlineCache(tmp_path)
    cache.write("SOLUSDT", "15m", [[1]])
    calls = {"n": 0}

    def busy(self, target):
        calls["n"] += 1
        raise PermissionError(32, "arquivo em uso")

    monkeypatch.setattr(Path, "replace", busy)
    monkeypatch.setattr("app.backtest.data.time.sleep", lambda s: None)
    assert cache.write("SOLUSDT", "15m", [[2]]) is False
    assert calls["n"] == KlineCache.REPLACE_ATTEMPTS
    assert "kline_cache.write_skipped" in caplog.text
    assert [p.name for p in tmp_path.iterdir()] == ["SOLUSDT_15m.json.gz"]  # sem .tmp
    monkeypatch.undo()
    assert cache.read("SOLUSDT", "15m") == [[1]]  # o arquivo anterior segue íntegro


def test_cache_write_succeeds_after_a_transient_lock(tmp_path, monkeypatch):
    from pathlib import Path

    cache = KlineCache(tmp_path)
    real_replace = Path.replace
    state = {"fail": 2}

    def flaky(self, target):
        if state["fail"]:
            state["fail"] -= 1
            raise PermissionError(5, "acesso negado")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr("app.backtest.data.time.sleep", lambda s: None)
    assert cache.write("AVAXUSDT", "4h", [[3]])
    monkeypatch.undo()
    assert cache.read("AVAXUSDT", "4h") == [[3]]


def test_concurrent_writers_leave_a_readable_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    cache = KlineCache(tmp_path)
    rows = [[[k, k]] * 200 for k in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda r: cache.write("ETHUSDT", "1h", r), rows))
    assert cache.read("ETHUSDT", "1h") in rows
    assert [p.name for p in tmp_path.iterdir()] == ["ETHUSDT_1h.json.gz"]
