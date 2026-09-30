import json
from typing import Self

import pytest
from websockets.exceptions import ConnectionClosed

from app.exchanges.binance.ws import BinanceWebSocket


class _FakeWS:
    def __init__(self, messages: list[str], *, close_after: bool = True) -> None:
        self._messages = messages
        self._close_after = close_after
        self.sent: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for m in self._messages:
            yield m
        if self._close_after:
            raise ConnectionClosed(None, None)

    async def send(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_stream_reconnects_after_close(monkeypatch):
    first = _FakeWS([json.dumps({"e": "24hrTicker", "s": "BTCUSDT"})])
    second = _FakeWS([json.dumps({"e": "24hrTicker", "s": "ETHUSDT"})])

    calls: list = []

    def fake_connect(url, **kwargs):
        calls.append(url)
        return first if len(calls) == 1 else second

    monkeypatch.setattr("app.exchanges.binance.ws.websockets.connect", fake_connect)

    ws = BinanceWebSocket(initial_backoff=0.0, max_backoff=0.0)
    gen = ws.stream("wss://fake/ws")
    try:
        m1 = await gen.__anext__()
        m2 = await gen.__anext__()
        assert m1["s"] == "BTCUSDT"
        assert m2["s"] == "ETHUSDT"
        assert len(calls) == 2
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_stream_responds_to_application_ping(monkeypatch):
    ws_fake = _FakeWS(
        [
            json.dumps({"ping": 12345}),
            json.dumps({"e": "24hrTicker", "s": "BTCUSDT"}),
        ]
    )
    monkeypatch.setattr(
        "app.exchanges.binance.ws.websockets.connect",
        lambda url, **kwargs: ws_fake,
    )

    ws = BinanceWebSocket(initial_backoff=0.0, max_backoff=0.0)
    gen = ws.stream("wss://fake/ws")
    try:
        msg = await gen.__anext__()
        assert msg["e"] == "24hrTicker"
        assert ws_fake.sent == [json.dumps({"pong": 12345})]
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_stream_skips_malformed_json(monkeypatch):
    ws_fake = _FakeWS(
        ["not-json", json.dumps({"e": "24hrTicker", "s": "BTCUSDT"})]
    )
    monkeypatch.setattr(
        "app.exchanges.binance.ws.websockets.connect",
        lambda url, **kwargs: ws_fake,
    )

    ws = BinanceWebSocket(initial_backoff=0.0, max_backoff=0.0)
    gen = ws.stream("wss://fake/ws")
    try:
        msg = await gen.__anext__()
        assert msg["e"] == "24hrTicker"
    finally:
        await gen.aclose()