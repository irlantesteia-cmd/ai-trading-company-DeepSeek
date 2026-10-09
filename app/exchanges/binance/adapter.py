from __future__ import annotations

from collections.abc import Callable

from app.core.config import settings
from app.core.enums import MarketType
from app.core.exceptions import ExchangeError
from app.domain.models.asset import TradingPair
from app.exchanges.base.account import AccountProvider
from app.exchanges.base.exchange import Exchange
from app.exchanges.base.market_data import MarketDataProvider
from app.exchanges.base.orders import OrderProvider
from app.exchanges.base.positions import PositionProvider
from app.exchanges.binance.account import BinanceAccountProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import map_trading_pair
from app.exchanges.binance.market_data import BinanceMarketDataProvider
from app.exchanges.binance.orders import BinanceOrderProvider
from app.exchanges.binance.positions import BinancePositionProvider
from app.exchanges.binance.ws import BinanceWebSocket

WS_SPOT_PROD = "wss://stream.binance.com:9443"
WS_SPOT_TESTNET = "wss://testnet.binance.vision"
WS_FUTURES_PROD = "wss://fstream.binance.com"
WS_FUTURES_TESTNET = "wss://stream.binancefuture.com"


class BinanceAdapter(Exchange):
    name = "binance"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool | None = None,
        orders_wrapper: Callable[[OrderProvider], OrderProvider] | None = None,
    ) -> None:
        """`orders_wrapper` envolve o provider de ordens antes de ele ser
        compartilhado com `positions` (ex.: `RecordingOrderProvider`), para que
        o fechamento de posição passe pelo mesmo caminho que as entradas.
        """
        testnet = settings.binance_testnet if testnet is None else testnet
        self._client = BinanceClient(
            api_key or settings.binance_api_key,
            api_secret or settings.binance_api_secret,
            testnet=testnet,
            timeout=settings.binance_timeout_s,
            max_retries=settings.binance_max_retries,
            recv_window_ms=settings.binance_recv_window_ms,
        )
        self._ws = BinanceWebSocket(
            initial_backoff=settings.binance_ws_reconnect_initial_s,
            max_backoff=settings.binance_ws_reconnect_max_s,
            ping_interval=settings.binance_ws_ping_interval_s,
            ping_timeout=settings.binance_ws_ping_timeout_s,
        )

        ws_bases = {
            MarketType.SPOT: WS_SPOT_TESTNET if testnet else WS_SPOT_PROD,
            MarketType.FUTURES: WS_FUTURES_TESTNET if testnet else WS_FUTURES_PROD,
        }

        orders: OrderProvider = BinanceOrderProvider(self._client)
        if orders_wrapper is not None:
            orders = orders_wrapper(orders)
        self._orders = orders
        self._market_data = BinanceMarketDataProvider(
            self._client,
            self._ws,
            ws_spot_base=ws_bases[MarketType.SPOT],
            ws_futures_base=ws_bases[MarketType.FUTURES],
        )
        self._account = BinanceAccountProvider(self._client)
        self._positions = BinancePositionProvider(self._client, self._orders)

    @property
    def market_data(self) -> MarketDataProvider:
        return self._market_data

    @property
    def account(self) -> AccountProvider:
        return self._account

    @property
    def orders(self) -> OrderProvider:
        return self._orders

    @property
    def positions(self) -> PositionProvider:
        return self._positions

    async def list_trading_pairs(self, market_type: MarketType) -> list[TradingPair]:
        path = (
            "/api/v3/exchangeInfo"
            if market_type is MarketType.SPOT
            else "/fapi/v1/exchangeInfo"
        )
        raw = await self._client.request("GET", path, market_type=market_type)
        return [
            map_trading_pair(sym, market_type)
            for sym in raw.get("symbols", [])
            if sym.get("status") == "TRADING"
        ]

    async def ping(self) -> bool:
        for market_type in (MarketType.SPOT, MarketType.FUTURES):
            path = "/api/v3/ping" if market_type is MarketType.SPOT else "/fapi/v1/ping"
            try:
                await self._client.request("GET", path, market_type=market_type)
            except ExchangeError:
                return False
        return True

    async def close(self) -> None:
        await self._client.close()