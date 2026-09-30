from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.core.enums import MarketType
from app.domain.models.market import (
    Candle,
    OrderBookSnapshot,
    StreamKind,
    Ticker,
    TradeTick,
)


class MarketDataProvider(ABC):
    """Fornece dados de mercado (REST e WebSocket) de forma normalizada."""

    @abstractmethod
    async def get_ticker(self, symbol: str, market_type: MarketType) -> Ticker: ...

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        interval: str,
        market_type: MarketType,
        limit: int = 500,
    ) -> list[Candle]: ...

    @abstractmethod
    async def get_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        depth: int = 20,
    ) -> OrderBookSnapshot: ...

    @abstractmethod
    def stream(
        self,
        symbol: str,
        kind: StreamKind,
        market_type: MarketType,
        interval: str | None = None,
    ) -> AsyncIterator[Ticker | Candle | OrderBookSnapshot | TradeTick]: ...