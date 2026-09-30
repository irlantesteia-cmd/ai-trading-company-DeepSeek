from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.core.enums import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from app.domain.models.base import DomainModel


class OrderRequest(DomainModel):
    """Intenção de ordem (pré-envio)."""

    client_order_id: str
    symbol: str
    market_type: MarketType
    side: OrderSide
    type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce | None = None
    reduce_only: bool = False
    position_side: PositionSide | None = None  # apenas FUTURES
    leverage: int | None = None  # apenas FUTURES


class OrderFill(DomainModel):
    price: Decimal
    quantity: Decimal
    commission: Decimal
    commission_asset: str
    timestamp: datetime


class Order(DomainModel):
    """Estado de uma ordem (retorno normalizado da exchange)."""

    exchange_order_id: str
    client_order_id: str
    symbol: str
    market_type: MarketType
    side: OrderSide
    type: OrderType
    status: OrderStatus
    quantity: Decimal
    executed_quantity: Decimal
    price: Decimal | None = None
    average_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce | None = None
    reduce_only: bool = False
    position_side: PositionSide | None = None
    created_at: datetime
    updated_at: datetime
    fills: list[OrderFill] = Field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.executed_quantity