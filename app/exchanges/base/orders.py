from abc import ABC, abstractmethod

from app.core.enums import MarketType
from app.domain.models.order import Order, OrderRequest


class OrderProvider(ABC):
    """Gerencia o ciclo de vida de ordens."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Order: ...

    @abstractmethod
    async def cancel_order(
        self,
        symbol: str,
        exchange_order_id: str,
        market_type: MarketType,
    ) -> Order: ...

    @abstractmethod
    async def get_order(
        self,
        symbol: str,
        exchange_order_id: str,
        market_type: MarketType,
    ) -> Order: ...

    @abstractmethod
    async def list_open_orders(
        self,
        symbol: str | None,
        market_type: MarketType,
    ) -> list[Order]: ...

    # Ordens condicionais (SL/TP). Opcionais: só providers com suporte
    # (hoje, Binance FUTURES via /fapi/v1/algoOrder) sobrescrevem.
    async def place_conditional_order(self, request: OrderRequest) -> Order:
        raise NotImplementedError(
            f"{type(self).__name__} não suporta ordens condicionais"
        )

    async def cancel_all_algo_orders(self, symbol: str) -> int:
        raise NotImplementedError(
            f"{type(self).__name__} não suporta ordens condicionais"
        )