from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    """Limites configuráveis do motor de risco.

    Todos os valores monetários em USDT.
    `correlated_groups` mapeia nome do grupo → lista de símbolos mutuamente correlacionados.
    """

    max_position_notional_per_symbol: Decimal = Decimal(10000)
    max_total_notional: Decimal = Decimal(50000)
    max_leverage: int = 10
    max_daily_loss: Decimal = Decimal(1000)
    max_open_positions: int = 5
    min_confidence: float = 0.5
    correlated_groups: dict[str, list[str]] = field(default_factory=dict)