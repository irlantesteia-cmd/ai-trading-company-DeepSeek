"""Cache em memória dos metadados de trading pairs.

A Binance rejeita ordens com quantity/price fora do `stepSize`/`tickSize`.
Este serviço carrega os `TradingPair` uma única vez (via
`Exchange.list_trading_pairs`) e expõe helpers de quantização.

Carregamento: `await service.load(exchange, market_type=FUTURES)` no boot.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from app.core.enums import MarketType
from app.domain.models.asset import TradingPair

if TYPE_CHECKING:
    from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)


class SymbolInfoService:
    """Cache de `TradingPair` por (symbol, market_type).

    Não é thread-safe (uso single-loop asyncio).
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, MarketType], TradingPair] = {}

    @property
    def size(self) -> int:
        return len(self._by_key)

    def has(self, symbol: str, market_type: MarketType) -> bool:
        return (symbol, market_type) in self._by_key

    def get(
        self, symbol: str, market_type: MarketType
    ) -> TradingPair | None:
        return self._by_key.get((symbol, market_type))

    def put(self, pair: TradingPair) -> None:
        self._by_key[(pair.symbol, pair.market_type)] = pair

    def put_many(self, pairs: list[TradingPair]) -> None:
        for p in pairs:
            self.put(p)

    async def load(
        self, exchange: Exchange, *, market_type: MarketType
    ) -> int:
        """Carrega os pares do mercado. Retorna quantos foram cacheados."""
        try:
            pairs = await exchange.list_trading_pairs(market_type)
        except Exception:
            logger.exception(
                "symbol_info.load_failed",
                extra={"market_type": market_type.value},
            )
            return 0
        self.put_many(pairs)
        logger.info(
            "symbol_info.loaded",
            extra={
                "market_type": market_type.value,
                "num_pairs": len(pairs),
            },
        )
        return len(pairs)

    # ---------------------------------------------------------- quantization
    def round_quantity(
        self,
        symbol: str,
        market_type: MarketType,
        quantity: Decimal,
    ) -> Decimal:
        """Quantiza quantity pelo stepSize. Se não houver info, devolve sem
        alteração (fallback — a exchange ainda pode rejeitar, mas é melhor
        logar do que esconder)."""
        pair = self.get(symbol, market_type)
        if pair is None or pair.step_size <= 0:
            logger.warning(
                "symbol_info.missing_step_size",
                extra={"symbol": symbol, "market_type": market_type.value},
            )
            return quantity
        return (quantity // pair.step_size) * pair.step_size

    def round_price(
        self,
        symbol: str,
        market_type: MarketType,
        price: Decimal,
    ) -> Decimal:
        """Quantiza price para `price_precision` casas decimais (ROUND_DOWN)."""
        pair = self.get(symbol, market_type)
        if pair is None:
            return price
        tick = Decimal(1).scaleb(-pair.price_precision)
        return (price // tick) * tick