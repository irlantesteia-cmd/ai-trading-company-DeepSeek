from abc import ABC, abstractmethod

from app.domain.models.position import FuturesPosition, SpotBalance


class AccountProvider(ABC):
    """Fornece dados de conta e posições."""

    @abstractmethod
    async def get_spot_balances(self) -> list[SpotBalance]: ...

    @abstractmethod
    async def get_futures_positions(
        self, symbol: str | None = None
    ) -> list[FuturesPosition]: ...

    @abstractmethod
    async def get_futures_balance(self, asset: str = "USDT") -> SpotBalance: ...