from sqlalchemy import select

from app.core.enums import MarketType
from app.database.models.asset import TradingPairORM
from app.database.repositories.base import BaseRepository


class TradingPairRepository(BaseRepository[TradingPairORM]):
    model = TradingPairORM

    async def get_by_symbol(
        self, symbol: str, market_type: MarketType
    ) -> TradingPairORM | None:
        stmt = select(self.model).where(
            self.model.symbol == symbol,
            self.model.market_type == market_type.value,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, pair: TradingPairORM) -> TradingPairORM:
        existing = await self.get_by_symbol(pair.symbol, MarketType(pair.market_type))
        if existing is None:
            return await self.add(pair)
        for field in (
            "price_precision",
            "quantity_precision",
            "min_notional",
            "min_quantity",
            "step_size",
            "status",
        ):
            setattr(existing, field, getattr(pair, field))
        await self.session.flush()
        return existing