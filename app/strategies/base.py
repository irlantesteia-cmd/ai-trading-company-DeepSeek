from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext


class Strategy(ABC):
    """Contrato base de toda estratégia plugável."""

    name: str

    @abstractmethod
    def generate(self, ctx: StrategyContext) -> Signal | None:
        """Analisa o contexto e devolve um `Signal` ou `None`."""

    @property
    def warmup(self) -> int:
        """Número mínimo de candles antes de gerar sinais."""
        return 0