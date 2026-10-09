from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.enums import MarginType, PositionSide
from app.domain.models.order import Order
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
        *,
        client_order_prefix: str = "close",
    ) -> Order | None:
        """Fecha a posição e retorna a `Order` de fechamento (com fills).

        `client_order_prefix` identifica o motivo no `clientOrderId`
        (`close-` = manual, `tx-` = saída por tempo; ver
        `CloseReason.from_client_order_id`).

        Retorna `None` se não havia posição aberta. O `Order` retornado
        carrega os fills já anexados (via `/fapi/v1/userTrades`), para que
        chamadores possam publicar `OrderFilled` no bus e manter o
        `RoundTripAgent` em sincronia.
        """
        ...