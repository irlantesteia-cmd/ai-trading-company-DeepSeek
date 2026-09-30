from datetime import datetime
from decimal import Decimal

from app.core.enums import MarketType, OrderSide, PositionSide
from app.domain.models.base import DomainModel


class Trade(DomainModel):
    """Registro de execução (fill agregado a uma posição ou trade fechado)."""

    trade_id: str
    order_id: str
    symbol: str
    market_type: MarketType
    side: OrderSide
    position_side: PositionSide | None = None
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    realized_pnl: Decimal | None = None  # apenas FUTURES
    timestamp: datetime


class RoundTrip(DomainModel):
    """Trade fechado: abertura + fechamento (útil para métricas)."""

    symbol: str
    market_type: MarketType
    direction: PositionSide
    opened_at: datetime
    closed_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal