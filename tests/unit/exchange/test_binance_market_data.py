import pytest
import respx
from httpx import Response

from app.core.enums import MarketType
from app.exchanges.binance.client import SPOT_REST_TESTNET, BinanceClient
from app.exchanges.binance.market_data import BinanceMarketDataProvider
from app.exchanges.binance.ws import BinanceWebSocket


def _provider() -> tuple[BinanceMarketDataProvider, BinanceClient]:
    client = BinanceClient(api_key="k", api_secret="s", testnet=True)
    provider = BinanceMarketDataProvider(
        client,
        BinanceWebSocket(),
        ws_spot_base="wss://testnet.binance.vision",
        ws_futures_base="wss://stream.binancefuture.com",
    )
    return provider, client


@pytest.mark.asyncio
async def test_get_ticker_spot():
    provider, client = _provider()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            mock.get("/api/v3/ticker/24hr").mock(
                return_value=Response(
                    200,
                    json={
                        "symbol": "BTCUSDT",
                        "bidPrice": "60000",
                        "askPrice": "60001",
                        "lastPrice": "60000.5",
                        "volume": "100",
                        "closeTime": 1_700_000_000_000,
                    },
                )
            )
            ticker = await provider.get_ticker("BTCUSDT", MarketType.SPOT)
            assert ticker.symbol == "BTCUSDT"
            assert ticker.last > 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_candles_spot():
    provider, client = _provider()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            mock.get("/api/v3/klines").mock(
                return_value=Response(
                    200,
                    json=[
                        [
                            1_700_000_000_000, "1.0", "2.0", "0.5", "1.5", "100.0",
                            1_700_000_059_999, "150.0", 42, "50.0", "75.0", "0",
                        ]
                    ],
                )
            )
            candles = await provider.get_candles("BTCUSDT", "1m", MarketType.SPOT)
            assert len(candles) == 1
            assert candles[0].interval == "1m"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_order_book_spot():
    provider, client = _provider()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            mock.get("/api/v3/depth").mock(
                return_value=Response(
                    200,
                    json={
                        "lastUpdateId": 1,
                        "bids": [["60000", "1.0"]],
                        "asks": [["60001", "0.5"]],
                    },
                )
            )
            ob = await provider.get_order_book("BTCUSDT", MarketType.SPOT)
            assert ob.last_update_id == 1
            assert len(ob.bids) == 1
    finally:
        await client.close()


def test_stream_name_builder():
    p, _ = _provider()
    assert p._build_stream_name("BTCUSDT", "ticker", None) == "btcusdt@ticker"
    assert p._build_stream_name("BTCUSDT", "kline", "5m") == "btcusdt@kline_5m"
    assert p._build_stream_name("BTCUSDT", "depth", None) == "btcusdt@depth20"
    assert p._build_stream_name("BTCUSDT", "aggTrade", None) == "btcusdt@aggTrade"


def test_stream_name_builder_kline_requires_interval():
    p, _ = _provider()
    with pytest.raises(ValueError):
        p._build_stream_name("BTCUSDT", "kline", None)