import hashlib
import hmac

import pytest
import respx
from httpx import Response

from app.core.enums import MarketType
from app.core.exceptions import (
    ExchangeAuthError,
    ExchangeConnectionError,
    ExchangeOrderRejectedError,
)
from app.exchanges.binance.client import (
    SPOT_REST_TESTNET,
    BinanceClient,
)

FIXED_SERVER_TIME_MS = 1_700_000_000_000


def _client(**overrides) -> BinanceClient:
    kwargs: dict = {
        "api_key": "key",
        "api_secret": "secret",
        "testnet": True,
        "max_retries": 1,
    }
    kwargs.update(overrides)
    return BinanceClient(**kwargs)


def _mock_server_time(mock, path: str = "/api/v3/time") -> None:
    mock.get(path).mock(
        return_value=Response(200, json={"serverTime": FIXED_SERVER_TIME_MS})
    )


def test_sign_query_is_hmac_sha256_of_string():
    c = _client()
    qs = "symbol=BTCUSDT&side=BUY&timestamp=1&recvWindow=5000"
    expected = hmac.new(b"secret", qs.encode(), hashlib.sha256).hexdigest()
    assert c._sign_query(qs) == expected


def test_build_url_puts_signature_last():
    c = _client()
    url = c._build_url(
        SPOT_REST_TESTNET,
        "/api/v3/account",
        {},
        signed=True,
        market_type=MarketType.SPOT,
    )
    assert url.startswith(f"{SPOT_REST_TESTNET}/api/v3/account?")
    assert "timestamp=" in url
    assert "recvWindow=" in url
    # signature é o último parâmetro
    tail = url.split("?", 1)[1]
    assert tail.split("&")[-1].startswith("signature=")


def test_build_url_unsigned_without_params():
    c = _client()
    url = c._build_url(
        SPOT_REST_TESTNET,
        "/api/v3/ping",
        {},
        signed=False,
        market_type=MarketType.SPOT,
    )
    assert url == f"{SPOT_REST_TESTNET}/api/v3/ping"


@pytest.mark.asyncio
async def test_sync_time_computes_offset():
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            _mock_server_time(mock)
            offset = await c.sync_time(MarketType.SPOT)
            assert c.time_offset_ms[MarketType.SPOT] == offset
            assert c._time_synced[MarketType.SPOT] is True
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_signed_request_requires_successful_time_sync():
    c = _client(time_sync_attempts=2)
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            mock.get("/api/v3/time").mock(
                side_effect=[
                    Response(500, text="boom"),
                    Response(500, text="boom"),
                ]
            )
            with pytest.raises(ExchangeConnectionError, match="time sync falhou"):
                await c.request(
                    "GET",
                    "/api/v3/account",
                    market_type=MarketType.SPOT,
                    signed=True,
                )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_signed_request_retries_time_sync_then_succeeds():
    c = _client(time_sync_attempts=2)
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            mock.get("/api/v3/time").mock(
                side_effect=[
                    Response(500, text="boom"),
                    Response(200, json={"serverTime": FIXED_SERVER_TIME_MS}),
                ]
            )
            mock.get("/api/v3/account").mock(
                return_value=Response(200, json={"balances": []})
            )
            result = await c.request(
                "GET",
                "/api/v3/account",
                market_type=MarketType.SPOT,
                signed=True,
            )
            assert result == {"balances": []}
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_signed_request_signature_matches_sent_query():
    """A assinatura enviada deve bater com a query string efetivamente transmitida.

    Este é o teste-chave do bug -1022: HMAC do que foi enviado == signature.
    """
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            _mock_server_time(mock)
            route = mock.get("/api/v3/account").mock(
                return_value=Response(200, json={"balances": []})
            )
            await c.request(
                "GET",
                "/api/v3/account",
                market_type=MarketType.SPOT,
                signed=True,
            )
            sent_url = str(route.calls[0].request.url)
            query = sent_url.split("?", 1)[1]
            parts = query.split("&")
            signature = parts[-1].split("=", 1)[1]
            payload = "&".join(parts[:-1])
            expected = hmac.new(
                b"secret", payload.encode(), hashlib.sha256
            ).hexdigest()
            assert signature == expected
            assert route.calls[0].request.headers["X-MBX-APIKEY"] == "key"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_request_retries_on_5xx():
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            mock.get("/api/v3/ping").mock(
                side_effect=[
                    Response(500, text="boom"),
                    Response(200, json={}),
                ]
            )
            result = await c.request(
                "GET", "/api/v3/ping", market_type=MarketType.SPOT
            )
            assert result == {}
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_auth_error_mapping():
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            _mock_server_time(mock)
            mock.get("/api/v3/account").mock(
                return_value=Response(
                    401,
                    json={"code": -2015, "msg": "Invalid API-key"},
                )
            )
            with pytest.raises(ExchangeAuthError):
                await c.request(
                    "GET",
                    "/api/v3/account",
                    market_type=MarketType.SPOT,
                    signed=True,
                )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_order_rejected_error_mapping():
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            _mock_server_time(mock)
            mock.post("/api/v3/order").mock(
                return_value=Response(
                    400,
                    json={"code": -2010, "msg": "Insufficient balance"},
                )
            )
            with pytest.raises(ExchangeOrderRejectedError):
                await c.request(
                    "POST",
                    "/api/v3/order",
                    market_type=MarketType.SPOT,
                    signed=True,
                )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_timestamp_error_1021_triggers_resync_and_retry():
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            time_route = mock.get("/api/v3/time").mock(
                return_value=Response(200, json={"serverTime": FIXED_SERVER_TIME_MS})
            )
            mock.post("/api/v3/order").mock(
                side_effect=[
                    Response(
                        400,
                        json={"code": -1021, "msg": "Timestamp out of recvWindow"},
                    ),
                    Response(200, json={"orderId": 42, "symbol": "BTCUSDT"}),
                ]
            )
            result = await c.request(
                "POST",
                "/api/v3/order",
                market_type=MarketType.SPOT,
                signed=True,
                params={"symbol": "BTCUSDT", "side": "BUY"},
            )
            assert result["orderId"] == 42
            assert time_route.call_count >= 2
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_timestamp_error_1022_triggers_resync_and_retry():
    """-1022 (assinatura inválida) também dispara re-sync + retry."""
    c = _client()
    try:
        with respx.mock(base_url=SPOT_REST_TESTNET) as mock:
            time_route = mock.get("/api/v3/time").mock(
                return_value=Response(200, json={"serverTime": FIXED_SERVER_TIME_MS})
            )
            mock.get("/api/v3/account").mock(
                side_effect=[
                    Response(
                        400,
                        json={
                            "code": -1022,
                            "msg": "Signature for this request is not valid.",
                        },
                    ),
                    Response(200, json={"balances": []}),
                ]
            )
            result = await c.request(
                "GET",
                "/api/v3/account",
                market_type=MarketType.SPOT,
                signed=True,
            )
            assert result == {"balances": []}
            assert time_route.call_count >= 2
    finally:
        await c.close()