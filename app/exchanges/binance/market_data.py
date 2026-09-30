from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.enums import MarketType
from app.domain.models.market import (
    Candle,
    OrderBookSnapshot,
    StreamKind,
    Ticker,
    TradeTick,
)
from app.exchanges.base.market_data import MarketDataProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import (
    map_kline,
    map_kline_event,
    map_order_book,
    map_ticker,
    map_ticker_event,
)
from app.exchanges.binance.ws import BinanceWebSocket


class BinanceMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        client: BinanceClient,
        ws: BinanceWebSocket,
        *,
        ws_spot_base: str,
        ws_futures_base: str,
    ) -> None:
        self._client = client
        self._ws = ws
        self._ws_bases = {
            MarketType.SPOT: ws_spot_base,
            MarketType.FUTURES: ws_futures_base,
        }

    # ---------------------------------------------------------------- REST
    async def get_ticker(self, symbol: str, market_type: MarketType) -> Ticker:
        path = "/api/v3/ticker/24hr" if market_type is MarketType.SPOT else "/fapi/v1/ticker/24hr"
        raw = await self._client.request(
            "GET",
            path,
            market_type=market_type,
            params={"symbol": symbol},
        )
        return map_ticker(raw, market_type)

    async def get_candles(
        self,
        symbol: str,
        interval: str,
        market_type: MarketType,
        limit: int = 500,
    ) -> list[Candle]:
        path = "/api/v3/klines" if market_type is MarketType.SPOT else "/fapi/v1/klines"
        raw = await self._client.request(
            "GET",
            path,
            market_type=market_type,
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        return [map_kline(row, market_type, symbol, interval) for row in raw]

    async def get_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        depth: int = 20,
    ) -> OrderBookSnapshot:
        path = "/api/v3/depth" if market_type is MarketType.SPOT else "/fapi/v1/depth"
        raw = await self._client.request(
            "GET",
            path,
            market_type=market_type,
            params={"symbol": symbol, "limit": depth},
        )
        return map_order_book(raw, symbol, market_type)

    # -------------------------------------------------------------- streams
    def stream(
        self,
        symbol: str,
        kind: StreamKind,
        market_type: MarketType,
        interval: str | None = None,
    ) -> AsyncIterator[Ticker | Candle | OrderBookSnapshot | TradeTick]:
        stream_name = self._build_stream_name(symbol, kind, interval)
        url = f"{self._ws_bases[market_type]}/ws/{stream_name}"
        return self._stream_iter(url, symbol, kind, market_type, interval)

    async def _stream_iter(
        self,
        url: str,
        symbol: str,
        kind: StreamKind,
        market_type: MarketType,
        interval: str | None,
    ) -> AsyncIterator[Ticker | Candle | OrderBookSnapshot | TradeTick]:
        async for msg in self._ws.stream(url):
            event = msg.get("e")
            if event == "24hrTicker":
                yield map_ticker_event(msg, symbol, market_type)
            elif event == "kline" and interval is not None:
                yield map_kline_event(msg, symbol, market_type, interval)
            elif event == "depthUpdate" or "lastUpdateId" in msg:
                yield map_order_book(msg, symbol, market_type)
            # aggTrade fica fora desta fase (será no data pipeline da Fase 6)

    @staticmethod
    def _build_stream_name(symbol: str, kind: StreamKind, interval: str | None) -> str:
        s = symbol.lower()
        if kind == "ticker":
            return f"{s}@ticker"
        if kind == "kline":
            if interval is None:
                raise ValueError("interval obrigatório para stream de kline")
            return f"{s}@kline_{interval}"
        if kind == "depth":
            return f"{s}@depth20"
        if kind == "aggTrade":
            return f"{s}@aggTrade"
        raise ValueError(f"kind de stream desconhecido: {kind}")