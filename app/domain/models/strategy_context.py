from __future__ import annotations

from dataclasses import dataclass, field

from app.core.enums import MarketType
from app.domain.models.market import Candle


@dataclass
class StrategyContext:
    """Entrada de uma estratégia: metadados + candles em ordem cronológica
    (mais antigo primeiro).

    `ref_candles`: candles de ativos de referência (ex.: BTC para alts).
    Chave é o símbolo. Vazio quando a estratégia não usa cross-asset.
    """

    symbol: str
    market_type: MarketType
    interval: str
    candles: list[Candle]
    ref_candles: dict[str, list[Candle]] = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    @property
    def closes(self) -> list[float]:
        return [float(c.close) for c in self.candles]

    @property
    def highs(self) -> list[float]:
        return [float(c.high) for c in self.candles]

    @property
    def lows(self) -> list[float]:
        return [float(c.low) for c in self.candles]

    @property
    def opens(self) -> list[float]:
        return [float(c.open) for c in self.candles]