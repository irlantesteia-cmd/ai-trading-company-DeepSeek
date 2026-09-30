from abc import ABC, abstractmethod

from app.core.enums import MarketType
from app.domain.models.asset import TradingPair
from app.exchanges.base.account import AccountProvider
from app.exchanges.base.market_data import MarketDataProvider
from app.exchanges.base.orders import OrderProvider
from app.exchanges.base.positions import PositionProvider


class Exchange(ABC):
    """Contrato raiz de uma exchange. Implementações devem isolar o SDK/HTTP."""

    name: str

    @property
    @abstractmethod
    def market_data(self) -> MarketDataProvider: ...

    @property
    @abstractmethod
    def account(self) -> AccountProvider: ...

    @property
    @abstractmethod
    def orders(self) -> OrderProvider: ...

    @property
    @abstractmethod
    def positions(self) -> PositionProvider: ...

    @abstractmethod
    async def list_trading_pairs(
        self, market_type: MarketType
    ) -> list[TradingPair]: ...

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...