from abc import ABC, abstractmethod

from app.core.enums import MarginType, PositionSide
from app.domain.models.position import FuturesPosition


class PositionProvider(ABC):
    """Gerencia posições em FUTURES (em SPOT, apenas holdings)."""

    @abstractmethod
    async def get_position(
        self,
        symbol: str,
        position_side: PositionSide | None = None,
    ) -> FuturesPosition | None: ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @abstractmethod
    async def set_margin_type(
        self, symbol: str, margin_type: MarginType
    ) -> None: ...

    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        position_side: PositionSide | None = None,
    ) -> None: ...