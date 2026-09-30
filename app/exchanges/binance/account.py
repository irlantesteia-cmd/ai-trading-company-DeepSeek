from __future__ import annotations

from app.core.enums import MarketType
from app.domain.models.position import FuturesPosition, SpotBalance
from app.exchanges.base.account import AccountProvider
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.mappers import (
    map_futures_balance,
    map_futures_position,
    map_spot_balance,
)


class BinanceAccountProvider(AccountProvider):
    def __init__(self, client: BinanceClient) -> None:
        self._client = client

    async def get_spot_balances(self) -> list[SpotBalance]:
        raw = await self._client.request(
            "GET",
            "/api/v3/account",
            market_type=MarketType.SPOT,
            signed=True,
        )
        return [
            map_spot_balance(b)
            for b in raw.get("balances", [])
            if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
        ]

    async def get_futures_positions(
        self,
        symbol: str | None = None,
    ) -> list[FuturesPosition]:
        params: dict = {}
        if symbol is not None:
            params["symbol"] = symbol
        raw = await self._client.request(
            "GET",
            "/fapi/v2/positionRisk",
            market_type=MarketType.FUTURES,
            signed=True,
            params=params,
        )
        positions = [map_futures_position(p) for p in raw]
        return [p for p in positions if p.is_open]

    async def get_futures_balance(self, asset: str = "USDT") -> SpotBalance:
        raw = await self._client.request(
            "GET",
            "/fapi/v2/balance",
            market_type=MarketType.FUTURES,
            signed=True,
        )
        for entry in raw:
            if entry["asset"] == asset:
                return map_futures_balance(entry)
        return SpotBalance(asset=asset, free=0, locked=0)  # type: ignore[arg-type]