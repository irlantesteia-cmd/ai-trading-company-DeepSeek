from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    """Limites configuráveis do motor de risco.

    Todos os valores monetários em USDT.
    `correlated_groups` mapeia nome do grupo → lista de símbolos mutuamente correlacionados.
    `max_same_direction` limita quantas posições abertas na **mesma direção**
    (LONG ou SHORT) o portfólio pode ter simultaneamente — evita concentração
    direcional quando vários símbolos geram sinal correlacionado no mesmo
    ciclo.
    """

    max_position_notional_per_symbol: Decimal = Decimal(10000)
    max_total_notional: Decimal = Decimal(50000)
    max_leverage: int = 10
    max_daily_loss: Decimal = Decimal(1000)
    # Perda diária máxima em % do equity (0 desliga). Vale o mais restritivo
    # entre este e `max_daily_loss`.
    max_daily_loss_pct: Decimal = Decimal(0)
    # Round trips seguidos com prejuízo antes de bloquear entradas (0 desliga).
    max_consecutive_losses: int = 0
    max_open_positions: int = 5
    max_same_direction: int = 2
    min_confidence: float = 0.5
    correlated_groups: dict[str, list[str]] = field(default_factory=dict)