import pytest
import respx
from httpx import Response

from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.orders import BinanceOrderProvider
from app.exchanges.binance.positions import BinancePositionProvider

FIXED_SERVER_TIME_MS = 1_700_000_000_000
FUTURES_BASE_URL = "https://demo-fapi.binance.com"


def _provider() -> tuple[BinancePositionProvider, BinanceClient]:
    client = BinanceClient(api_key="k", api_secret="s", testnet=True)
    orders = BinanceOrderProvider(client)
    positions = BinancePositionProvider(client, orders)
    return positions, client


def _mock_futures_time(mock) -> None:
    mock.get("/fapi/v1/time").mock(
        return_value=Response(200, json={"serverTime": FIXED_SERVER_TIME_MS})
    )


def _position_json(
    symbol: str = "SOLUSDT",
    position_side: str = "BOTH",
    quantity: str = "84.35",
) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "positionAmt": quantity,
            "entryPrice": "118.55",
            "markPrice": "118.16",
            "unRealizedProfit": "-33.57",
            "leverage": "20",
            "marginType": "cross",
            "isolatedMargin": "0",
            "liquidationPrice": "0",
            "positionSide": position_side,
            "updateTime": 1_700_000_000_000,
        }
    ]


@pytest.mark.asyncio
async def test_is_hedge_mode_queries_once_and_caches():
    positions, client = _provider()
    try:
        with respx.mock(base_url=FUTURES_BASE_URL) as mock:
            _mock_futures_time(mock)
            route = mock.get("/fapi/v1/positionSide/dual").mock(
                return_value=Response(200, json={"dualSidePosition": True})
            )
            assert await positions.is_hedge_mode() is True
            assert await positions.is_hedge_mode() is True
            assert route.call_count == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_position_one_way_uses_reduce_only():
    """One-way mode: reduceOnly=True, sem positionSide."""
    positions, client = _provider()
    try:
        with respx.mock(base_url=FUTURES_BASE_URL) as mock:
            _mock_futures_time(mock)
            mock.get("/fapi/v1/positionSide/dual").mock(
                return_value=Response(200, json={"dualSidePosition": False})
            )
            mock.get("/fapi/v2/positionRisk").mock(
                return_value=Response(200, json=_position_json())
            )
            order_route = mock.post("/fapi/v1/order").mock(
                return_value=Response(
                    200,
                    json={
                        "orderId": 1,
                        "clientOrderId": "c-1",
                        "symbol": "SOLUSDT",
                        "side": "SELL",
                        "type": "MARKET",
                        "status": "NEW",
                        "origQty": "84.35",
                        "executedQty": "0",
                        "time": 1_700_000_000_000,
                        "updateTime": 1_700_000_000_000,
                    },
                )
            )

            await positions.close_position("SOLUSDT")

            sent_url = str(order_route.calls[0].request.url)
            assert "reduceOnly=true" in sent_url
            assert "positionSide" not in sent_url
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_position_hedge_uses_position_side():
    """Hedge mode: positionSide=LONG, sem reduceOnly."""
    positions, client = _provider()
    try:
        with respx.mock(base_url=FUTURES_BASE_URL) as mock:
            _mock_futures_time(mock)
            mock.get("/fapi/v1/positionSide/dual").mock(
                return_value=Response(200, json={"dualSidePosition": True})
            )
            mock.get("/fapi/v2/positionRisk").mock(
                return_value=Response(
                    200, json=_position_json(position_side="LONG")
                )
            )
            order_route = mock.post("/fapi/v1/order").mock(
                return_value=Response(
                    200,
                    json={
                        "orderId": 1,
                        "clientOrderId": "c-1",
                        "symbol": "SOLUSDT",
                        "side": "SELL",
                        "type": "MARKET",
                        "status": "NEW",
                        "origQty": "84.35",
                        "executedQty": "0",
                        "time": 1_700_000_000_000,
                        "updateTime": 1_700_000_000_000,
                    },
                )
            )

            await positions.close_position("SOLUSDT")

            sent_url = str(order_route.calls[0].request.url)
            assert "positionSide=LONG" in sent_url
            assert "reduceOnly" not in sent_url
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_position_noop_when_flat():
    """Sem posição aberta → não envia ordem.

    Aqui usamos `assert_all_called=False` porque queremos que a rota POST
    exista para captura (se for chamada, o teste falha no `assert`),
    mas o comportamento esperado é que ela NÃO seja chamada.
    """
    positions, client = _provider()
    try:
        with respx.mock(
            base_url=FUTURES_BASE_URL, assert_all_called=False
        ) as mock:
            _mock_futures_time(mock)
            mock.get("/fapi/v2/positionRisk").mock(
                return_value=Response(
                    200, json=_position_json(quantity="0")
                )
            )
            order_route = mock.post("/fapi/v1/order").mock(
                return_value=Response(200, json={})
            )

            await positions.close_position("SOLUSDT")

            assert order_route.call_count == 0
    finally:
        await client.close()