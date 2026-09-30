from __future__ import annotations

from uuid import uuid4

from app.core.enums import MarginType, MarketType, OrderSide, OrderType, PositionSide
from app.core.exceptions import ExchangeError
from app.domain.models.order import OrderRequest
from app.domain.models.position import FuturesPosition
from app.exchanges.base.positions import PositionProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import map_futures_position
from app.exchanges.binance.orders import BinanceOrderProvider


class BinancePositionProvider(PositionProvider):
    """Provider de posições FUTURES. SPOT não tem posições — só holdings."""

    def __init__(self, client: BinanceClient, orders: BinanceOrderProvider) -> None:
        self._client = client
        self._orders = orders

    async def get_position(
        self,
        symbol: str,
        position_side: PositionSide | None = None,
    ) -> FuturesPosition | None:
        raw = await self._client.request(
            "GET",
            "/fapi/v2/positionRisk",
            market_type=MarketType.FUTURES,
            signed=True,
            params={"symbol": symbol},
        )
        for entry in raw:
            pos = map_futures_position(entry)
            if not pos.is_open:
                continue
            if position_side is None or pos.position_side == position_side:
                return pos
        return None

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._client.request(
            "POST",
            "/fapi/v1/leverage",
            market_type=MarketType.FUTURES,
            signed=True,
            params={"symbol": symbol, "leverage": leverage},
        )

    async def set_margin_type(self, symbol: str, margin_type: MarginType) -> None:
        try:
            await self._client.request(
                "POST",
                "/fapi/v1/marginType",
                market_type=MarketType.FUTURES,
                signed=True,
                params={"symbol": symbol, "marginType": margin_type.value},
            )
        except ExchangeError as exc:
            # Binance retorna -4046 se o margin type já está configurado — ignorar.
            if "-4046" in str(exc):
                return
            raise

    async def close_position(
        self,
        symbol: str,
        position_side: PositionSide | None = None,
    ) -> None:
        pos = await self.get_position(symbol, position_side)
        if pos is None:
            return
        side = (
            OrderSide.SELL
            if pos.position_side == PositionSide.LONG
            else OrderSide.BUY
        )
        await self._orders.place_order(
            OrderRequest(
                client_order_id=f"close-{uuid4().hex[:16]}",
                symbol=symbol,
                market_type=MarketType.FUTURES,
                side=side,
                type=OrderType.MARKET,
                quantity=pos.quantity,
                reduce_only=True,
                position_side=pos.position_side,
            )
        )