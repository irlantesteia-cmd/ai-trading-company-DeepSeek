from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from scripts.repair_open_candles import fetch_closed_candle, to_row

_OPEN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_OPEN_MS = int(_OPEN.timestamp() * 1000)


def _row(market_type: str = "FUTURES"):
    return SimpleNamespace(
        symbol="BTCUSDT", market_type=market_type, interval="5m", open_time=_OPEN
    )


def _kline(open_ms: int) -> list:
    return [open_ms, "1", "2", "0.5", "1.5", "7655.78", open_ms + 299_999, "0", 10, "40"]


def _client(payload, seen: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_closed_candle_requests_exact_open_time_on_demo():
    seen: list = []
    async with _client([_kline(_OPEN_MS)], seen) as client:
        candle = await fetch_closed_candle(client, _row(), testnet=True)

    assert candle is not None
    assert candle.volume == Decimal("7655.78")
    assert candle.closed is True
    req = seen[0]
    assert req.url.host == "demo-fapi.binance.com"
    assert req.url.path == "/fapi/v1/klines"
    assert req.url.params["startTime"] == str(_OPEN_MS)
    assert req.url.params["endTime"] == str(_OPEN_MS)


async def test_fetch_closed_candle_uses_spot_endpoint():
    seen: list = []
    async with _client([_kline(_OPEN_MS)], seen) as client:
        await fetch_closed_candle(client, _row("SPOT"), testnet=False)
    assert seen[0].url.host == "api.binance.com"
    assert seen[0].url.path == "/api/v3/klines"


@pytest.mark.parametrize("payload", [[], [_kline(_OPEN_MS + 300_000)]])
async def test_fetch_closed_candle_none_when_api_lacks_exact_candle(payload):
    async with _client(payload, []) as client:
        assert await fetch_closed_candle(client, _row(), testnet=True) is None


async def test_fetch_closed_candle_none_when_still_in_progress():
    now = _OPEN + timedelta(minutes=2)
    async with _client([_kline(_OPEN_MS)], []) as client:
        assert await fetch_closed_candle(client, _row(), testnet=True, now=now) is None


async def test_to_row_matches_upsert_columns():
    async with _client([_kline(_OPEN_MS)], []) as client:
        candle = await fetch_closed_candle(client, _row(), testnet=True)
    row = to_row(candle)
    assert row["market_type"] == "FUTURES"
    assert row["taker_buy_base_volume"] == Decimal(40)
    assert set(row) == {
        "symbol", "market_type", "interval", "open_time", "close_time", "open",
        "high", "low", "close", "volume", "trades", "taker_buy_base_volume",
    }
