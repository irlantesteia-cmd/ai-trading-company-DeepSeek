from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import MarketType
from app.database.models.order import OrderORM
from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    db_open_orders: list[str] = field(default_factory=list)
    exchange_open_orders: list[str] = field(default_factory=list)
    missing_on_exchange: list[str] = field(default_factory=list)  # DB sim, exchange não
    missing_in_db: list[str] = field(default_factory=list)        # exchange sim, DB não
    balanced: bool = True

    def as_dict(self) -> dict:
        return {
            "checked_at": self.checked_at.isoformat(),
            "db_open_orders": self.db_open_orders,
            "exchange_open_orders": self.exchange_open_orders,
            "missing_on_exchange": self.missing_on_exchange,
            "missing_in_db": self.missing_in_db,
            "balanced": self.balanced,
        }


class Reconciler:
    """Compara ordens abertas no DB com a exchange.

    Não corrige automaticamente — apenas reporta. A auto-correção entra na
    Fase 8, com revisão via PR controlado.
    """

    def __init__(
        self,
        *,
        exchange: Exchange,
        session_factory: async_sessionmaker,
    ) -> None:
        self._exchange = exchange
        self._session_factory = session_factory

    async def reconcile(
        self,
        *,
        symbol: str | None = None,
        market_type: MarketType = MarketType.FUTURES,
    ) -> ReconciliationReport:
        db_ids = await self._load_db_open_orders(
            symbol=symbol, market_type=market_type
        )
        exchange_ids = await self._load_exchange_open_orders(
            symbol=symbol, market_type=market_type
        )

        report = ReconciliationReport(
            db_open_orders=sorted(db_ids),
            exchange_open_orders=sorted(exchange_ids),
        )
        report.missing_on_exchange = sorted(db_ids - exchange_ids)
        report.missing_in_db = sorted(exchange_ids - db_ids)
        report.balanced = not report.missing_on_exchange and not report.missing_in_db

        if not report.balanced:
            logger.warning(
                "reconcile.divergence",
                extra={
                    "missing_on_exchange": report.missing_on_exchange,
                    "missing_in_db": report.missing_in_db,
                },
            )
        return report

    async def _load_db_open_orders(
        self, *, symbol: str | None, market_type: MarketType
    ) -> set[str]:
        stmt = select(OrderORM).where(
            OrderORM.status.in_(("NEW", "PARTIALLY_FILLED")),
            OrderORM.market_type == market_type.value,
        )
        if symbol is not None:
            stmt = stmt.where(OrderORM.symbol == symbol)

        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return {row.exchange_order_id for row in rows}

    async def _load_exchange_open_orders(
        self, *, symbol: str | None, market_type: MarketType
    ) -> set[str]:
        orders = await self._exchange.orders.list_open_orders(symbol, market_type)
        return {o.exchange_order_id for o in orders}