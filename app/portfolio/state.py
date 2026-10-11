from decimal import Decimal

from pydantic import Field

from app.domain.models.base import DomainModel
from app.domain.models.position import FuturesPosition


class HaltInfo(DomainModel):
    """Trava de trading ativa (tabela `trading_halts`)."""

    scope: str  # GLOBAL / SYMBOL
    symbol: str | None = None
    rule: str
    reason: str


class PortfolioState(DomainModel):
    """Snapshot do portfólio usado pelo motor de risco e pelo dimensionador."""

    equity: Decimal
    # P&L do dia (00:00 UTC): realizado (round trips fechados) + não realizado
    # (posições abertas). É o que o limite de perda diária avalia.
    daily_pnl: Decimal
    positions: list[FuturesPosition] = Field(default_factory=list)
    # Round trips fechados com prejuízo em sequência (do mais recente para trás).
    consecutive_losses: int = 0
    active_halts: list[HaltInfo] = Field(default_factory=list)

    def halt_for(self, symbol: str) -> HaltInfo | None:
        """Trava que bloqueia `symbol`: global primeiro, depois a do símbolo."""
        for halt in self.active_halts:
            if halt.scope == "GLOBAL":
                return halt
        for halt in self.active_halts:
            if halt.scope == "SYMBOL" and halt.symbol == symbol:
                return halt
        return None

    @property
    def open_positions(self) -> list[FuturesPosition]:
        return [p for p in self.positions if p.is_open]

    @property
    def open_positions_count(self) -> int:
        return len(self.open_positions)

    @property
    def total_notional(self) -> Decimal:
        return sum((p.notional for p in self.open_positions), Decimal(0))

    def notional_for(self, symbol: str) -> Decimal:
        return sum(
            (p.notional for p in self.open_positions if p.symbol == symbol),
            Decimal(0),
        )

    def has_position(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self.open_positions)

    def symbols(self) -> set[str]:
        return {p.symbol for p in self.open_positions}

    def count_in_direction(self, direction: str) -> int:
        """Conta posições abertas na direção `LONG` ou `SHORT`.

        `direction` é o valor textual (`PositionSide.LONG.value` /
        `PositionSide.SHORT.value`). Case-sensitive por design.
        """
        return sum(
            1
            for p in self.open_positions
            if p.position_side.value == direction
        )