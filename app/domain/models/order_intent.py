from decimal import Decimal
from uuid import uuid4

from pydantic import Field

from app.core.enums import MarketType, OrderSide, OrderType
from app.domain.models.base import DomainModel


class OrderIntent(DomainModel):
    """Intenção de ordem validada pelo pipeline (risco + dimensionamento)."""

    intent_id: str = Field(default_factory=lambda: str(uuid4()))
    signal_id: str
    symbol: str
    market_type: MarketType
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None
    reason: str = ""
    agent: str = ""