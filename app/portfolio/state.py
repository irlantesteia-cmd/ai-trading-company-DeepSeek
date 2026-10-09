from decimal import Decimal

from pydantic import Field

from app.domain.models.base import DomainModel
from app.domain.models.position import FuturesPosition


class PortfolioState(DomainModel):
    """Snapshot do portfólio usado pelo motor de risco e pelo dimensionador."""

    equity: Decimal
    daily_pnl: Decimal
    positions: list[FuturesPosition] = Field(default_factory=list)

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