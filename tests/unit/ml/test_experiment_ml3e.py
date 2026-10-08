"""ML-3e (funding / premium) no script de spike: alinhamento e paginação."""

from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pytest

from app.features.pipeline import default_pipeline
from scripts.experiment_horizon import (
    ConcatPipeline,
    MarketExtras,
    _fetch_funding,
    _fetch_raw_klines,
    _pipeline_for,
    _to_ms,
    funding_asof,
    funding_pipeline,
    premium_by_open,
    premium_pipeline,
)
from tests.unit.strategies.conftest import make_candles

_H8 = 8 * 60 * 60 * 1000


def test_funding_asof_uses_last_settled_rate():
    funding = [(1_000, 0.0001), (2_000, 0.0003), (3_000, -0.0002)]
    rate, change = funding_asof([999, 1_000, 1_500, 2_999, 3_000, 9_000], funding)
    assert rate == [None, 0.0001, 0.0001, 0.0003, -0.0002, -0.0002]
    assert change[0] is None
    assert change[1] is None  # só uma liquidação conhecida
    assert change[3] == pytest.approx(0.0002)
    assert change[4] == pytest.approx(-0.0005)


def test_funding_asof_excludes_settlement_after_candle_close():
    # Candle 07:55–07:59:59.999 fecha 1 ms antes da liquidação das 08:00.
    settle = 1_700_006_400_000
    funding = [(settle - _H8, 0.0001), (settle, 0.0009)]
    rate, _ = funding_asof([settle - 1, settle + 299_999], funding)
    assert rate == [0.0001, 0.0009]


def test_funding_asof_empty_series():
    rate, change = funding_asof([1, 2], [])
    assert rate == [None, None]
    assert change == [None, None]


def test_premium_by_open_maps_close_column():
    raw = [[1_000, "0.1", "0.2", "0.0", "0.15", "0", 1_299], [1_300, "0", "0", "0", "-0.05"]]
    assert premium_by_open(raw) == {1_000: 0.15, 1_300: -0.05}


def test_premium_pipeline_aligns_by_open_time():
    candles = make_candles([100.0 + i for i in range(5)])
    premium = {_to_ms(c.open_time): float(i) for i, c in enumerate(candles)}
    del premium[_to_ms(candles[2].open_time)]
    fm = premium_pipeline(premium).transform(candles)
    assert fm.indices == [0, 1, 3, 4]
    assert fm.values[:, 0].tolist() == [0.0, 1.0, 3.0, 4.0]


def test_concat_pipeline_keeps_rows_valid_in_both():
    candles = make_candles([100.0 + i * 0.5 for i in range(80)])
    base = default_pipeline()
    premium = {_to_ms(c.open_time): 0.001 for c in candles[50:]}
    pipe = ConcatPipeline(base, premium_pipeline(premium))
    fm = pipe.transform(candles)
    base_fm = base.transform(candles)

    assert fm.names == [*base.names, "premium_close"]
    assert fm.indices == [i for i in base_fm.indices if i >= 50]
    k = base_fm.indices.index(fm.indices[0])
    np.testing.assert_array_equal(fm.values[0, :-1], base_fm.values[k])
    assert fm.values[0, -1] == 0.001


def test_funding_pipeline_no_lookahead():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = make_candles([100.0] * 200, start=start)
    funding = [
        (_to_ms(start + timedelta(hours=8 * k)), 0.0001 * (k + 1)) for k in range(4)
    ]
    full = funding_pipeline(funding).transform(candles)
    # Truncar o futuro não pode mudar nenhuma linha já calculada.
    cut = 120
    partial = funding_pipeline(funding).transform(candles[:cut])
    for j, idx in enumerate(partial.indices):
        k = full.indices.index(idx)
        np.testing.assert_array_equal(partial.values[j], full.values[k])


def test_pipeline_for_feature_counts():
    extras = MarketExtras(funding=[(0, 0.0001)], premium_by_open_ms={0: 0.0})
    assert len(_pipeline_for("ohlcv", extras).names) == 8
    assert len(_pipeline_for("taker_buy", extras).names) == 9
    assert len(_pipeline_for("funding", extras).names) == 10
    assert len(_pipeline_for("premium", extras).names) == 9


async def test_fetch_funding_paginates_forward(monkeypatch):
    monkeypatch.setattr("scripts.experiment_horizon._FUNDING_PAGE_SIZE", 2)
    data = [{"fundingTime": t, "fundingRate": str(t / 1e7)} for t in (10, 20, 30, 40, 50)]
    seen_starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startTime"])
        end = int(request.url.params["endTime"])
        limit = int(request.url.params["limit"])
        seen_starts.append(start)
        page = [d for d in data if start <= d["fundingTime"] <= end][:limit]
        return httpx.Response(200, json=page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await _fetch_funding(
            client, base_url="https://x", symbol="BTCUSDT", start_ms=0, end_ms=45
        )

    assert [t for t, _ in out] == [10, 20, 30, 40]
    assert seen_starts == [0, 21, 41]


async def test_fetch_raw_klines_paginates_backward(monkeypatch):
    monkeypatch.setattr("scripts.experiment_horizon._BINANCE_PAGE_SIZE", 3)
    rows = [[t, "0", "0", "0", str(t)] for t in range(0, 70, 10)]
    seen_ends: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/premiumIndexKlines"
        limit = int(request.url.params["limit"])
        seen_ends.append(request.url.params.get("endTime"))
        end = int(request.url.params.get("endTime", 10**9))
        eligible = [r for r in rows if r[0] <= end]
        return httpx.Response(200, json=eligible[-limit:])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await _fetch_raw_klines(
            client,
            base_url="https://x",
            path="/fapi/v1/premiumIndexKlines",
            symbol="BTCUSDT",
            interval="5m",
            total_limit=5,
        )

    assert [r[0] for r in out] == [20, 30, 40, 50, 60]
    assert seen_ends == [None, "39"]
