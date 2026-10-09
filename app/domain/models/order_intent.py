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
    target_price: Decimal | None = None
    take_profit: Decimal | None = None
    # Preço de entrada usado no dimensionamento (close do sinal). SL/TP são
    # reancorados no preço real do fill mantendo a distância até ele.
    reference_price: Decimal | None = None
    reason: str = ""
    agent: str = ""

    @property
    def client_order_id(self) -> str:
        """`clientOrderId` da ordem de entrada (liga `signals` a `orders`)."""
        return client_order_id_for_intent(self.intent_id)


def client_order_id_for_intent(intent_id: str) -> str:
    return f"ai-{intent_id[:24]}"
