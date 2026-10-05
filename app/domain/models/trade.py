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
    round_trip_id: str | None = None
    role: str = "UNKNOWN"


class RoundTrip(DomainModel):
    """Trade fechado: abertura + fechamento (útil para métricas)."""

    round_trip_id: str
    symbol: str
    market_type: MarketType
    position_side: PositionSide
    status: str  # OPEN / CLOSED
    close_reason: str | None = None
    entry_quantity: Decimal
    entry_avg_price: Decimal
    entry_fee: Decimal = Decimal(0)
    exit_quantity: Decimal = Decimal(0)
    exit_avg_price: Decimal | None = None
    exit_fee: Decimal = Decimal(0)
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    opened_at: datetime
    closed_at: datetime | None = None