from decimal import Decimal

from app.core.enums import MarketType
from app.domain.models.base import DomainModel


class Asset(DomainModel):
    """Ativo negociável (ex.: BTC, ETH)."""

    symbol: str  # "BTC"
    base: str  # "BTC"
    quote: str  # "USDT"


class TradingPair(DomainModel):
    """Par negociável em um tipo de mercado."""

    symbol: str  # "BTCUSDT"
    base: str
    quote: str
    market_type: MarketType
    price_precision: int
    quantity_precision: int
    min_notional: Decimal
    min_quantity: Decimal
    step_size: Decimal
    status: str = "TRADING"