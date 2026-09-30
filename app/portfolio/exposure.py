from __future__ import annotations

from decimal import Decimal

from app.portfolio.state import PortfolioState


class ExposureTracker:
    """Consultas de exposição consolidada. Stateless — opera sobre PortfolioState."""

    @staticmethod
    def total_notional(state: PortfolioState) -> Decimal:
        return state.total_notional

    @staticmethod
    def by_symbol(state: PortfolioState) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for p in state.open_positions:
            result[p.symbol] = result.get(p.symbol, Decimal(0)) + p.notional
        return result

    @staticmethod
    def by_group(
        state: PortfolioState,
        groups: dict[str, list[str]],
    ) -> dict[str, Decimal]:
        by_sym = ExposureTracker.by_symbol(state)
        return {
            group: sum((by_sym.get(s, Decimal(0)) for s in symbols), Decimal(0))
            for group, symbols in groups.items()
        }