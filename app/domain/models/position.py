from datetime import datetime
from decimal import Decimal

from app.core.enums import MarginType, PositionSide
from app.domain.models.base import DomainModel


class SpotBalance(DomainModel):
    """Saldo em conta SPOT (não há 'posição' propriamente dita)."""

    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


class FuturesPosition(DomainModel):
    """Posição em USDⓈ-M FUTURES."""

    symbol: str
    position_side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    leverage: int
    margin_type: MarginType
    isolated_margin: Decimal
    liquidation_price: Decimal | None = None
    updated_at: datetime

    @property
    def notional(self) -> Decimal:
        return abs(self.quantity) * self.mark_price

    @property
    def is_open(self) -> bool:
        return self.quantity != 0