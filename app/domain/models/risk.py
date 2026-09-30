from decimal import Decimal

from app.core.enums import RiskAction
from app.domain.models.base import DomainModel


class RiskDecision(DomainModel):
    """Resultado da avaliação de risco sobre um sinal."""

    signal_id: str
    action: RiskAction
    reason: str
    approved_quantity: Decimal | None = None
    max_leverage: int | None = None
    stop_loss_price: Decimal | None = None