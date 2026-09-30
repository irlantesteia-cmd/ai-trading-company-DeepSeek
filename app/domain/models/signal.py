from datetime import datetime
from decimal import Decimal

from app.core.enums import MarketRegime, MarketType, SignalDirection
from app.domain.models.base import DomainModel


class Signal(DomainModel):
    """Sinal gerado por um agente/estratégia."""

    signal_id: str
    symbol: str
    market_type: MarketType
    direction: SignalDirection
    confidence: float  # 0.0 .. 1.0
    horizon: str  # "1m", "5m", "1h"
    suggested_entry: Decimal | None = None
    suggested_stop: Decimal | None = None
    suggested_target: Decimal | None = None
    regime: MarketRegime = MarketRegime.UNKNOWN
    strategy: str
    agent: str
    rationale: str | None = None
    generated_at: datetime