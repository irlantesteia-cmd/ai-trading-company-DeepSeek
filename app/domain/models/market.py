from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.core.enums import MarketType
from app.domain.models.base import DomainModel


class Ticker(DomainModel):
    symbol: str
    market_type: MarketType
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume_24h: Decimal
    timestamp: datetime


class Candle(DomainModel):
    symbol: str
    market_type: MarketType
    interval: str  # "1m", "5m", "1h", etc.
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int
    closed: bool = True


class OrderBookLevel(DomainModel):
    price: Decimal
    quantity: Decimal


class OrderBookSnapshot(DomainModel):
    symbol: str
    market_type: MarketType
    timestamp: datetime
    last_update_id: int
    bids: list[OrderBookLevel]  # ordenado do maior preço
    asks: list[OrderBookLevel]  # ordenado do menor preço


class TradeTick(DomainModel):
    symbol: str
    market_type: MarketType
    price: Decimal
    quantity: Decimal
    is_buyer_maker: bool
    timestamp: datetime
    trade_id: str


StreamKind = Literal["ticker", "kline", "depth", "aggTrade"]