from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import MarketType
from app.database.models.order import OrderORM
from app.domain.models.order import Order
from app.exchanges.base.exchange import Exchange
from app.runtime.order_recorder import OrderRecorder

logger = logging.getLogger(__name__)

# (exchange_order_id, is_conditional): ordens algo usam o algoId, que pode
# colidir com um orderId comum, então o tipo faz parte da chave.
_OrderKey = tuple[str, bool]


def _label(key: _OrderKey) -> str:
    order_id, is_conditional = key
    return f"algo:{order_id}" if is_conditional else order_id


@dataclass
class ReconciliationReport:
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    db_open_orders: list[str] = field(default_factory=list)
    exchange_open_orders: list[str] = field(default_factory=list)
    missing_on_exchange: list[str] = field(default_factory=list)  # DB sim, exchange não
    missing_in_db: list[str] = field(default_factory=list)        # exchange sim, DB não
    synced: list[str] = field(default_factory=list)      # divergências corrigidas no DB
    unresolved: list[str] = field(default_factory=list)  # divergências que persistem
    balanced: bool = True

    def as_dict(self) -> dict:
        return {
            "checked_at": self.checked_at.isoformat(),
            "db_open_orders": self.db_open_orders,
            "exchange_open_orders": self.exchange_open_orders,
            "missing_on_exchange": self.missing_on_exchange,
            "missing_in_db": self.missing_in_db,
            "synced": self.synced,
            "unresolved": self.unresolved,
            "balanced": self.balanced,
        }


class Reconciler:
    """Compara ordens abertas no DB (`orders`) com a exchange, incluindo as
    ordens condicionais (SL/TP algo) quando o provider as suporta.

    Com `recorder`, corrige o **registro** no DB (nunca age na exchange):
    - aberta no DB e ausente na exchange → busca o status final e grava
      (ex.: SL disparado: algo FINISHED → FILLED);
    - aberta na exchange e ausente no DB → grava (ordens anteriores à
      persistência ou criadas fora do bot).

    Sem `recorder`, só reporta. `balanced` é False apenas se sobrar alguma
    divergência não resolvida.
    """

    def __init__(
        self,
        *,
        exchange: Exchange,
        session_factory: async_sessionmaker,
        recorder: OrderRecorder | None = None,
    ) -> None:
        self._exchange = exchange
        self._session_factory = session_factory
        self._recorder = recorder

    async def reconcile(
        self,
        *,
        symbol: str | None = None,
        market_type: MarketType = MarketType.FUTURES,
    ) -> ReconciliationReport:
        exchange_orders, include_conditional = await self._load_exchange_open_orders(
            symbol=symbol, market_type=market_type
        )
        db_orders = await self._load_db_open_orders(
            symbol=symbol,
            market_type=market_type,
            include_conditional=include_conditional,
        )

        db_keys = set(db_orders)
        exchange_keys = set(exchange_orders)
        report = ReconciliationReport(
            db_open_orders=sorted(_label(k) for k in db_keys),
            exchange_open_orders=sorted(_label(k) for k in exchange_keys),
        )
        missing_on_exchange = sorted(db_keys - exchange_keys)
        missing_in_db = sorted(exchange_keys - db_keys)
        report.missing_on_exchange = [_label(k) for k in missing_on_exchange]
        report.missing_in_db = [_label(k) for k in missing_in_db]

        if self._recorder is None:
            report.unresolved = report.missing_on_exchange + report.missing_in_db
        else:
            for key in missing_on_exchange:
                ok = await self._sync_final_status(
                    key, symbol=db_orders[key], market_type=market_type
                )
                (report.synced if ok else report.unresolved).append(_label(key))
            for key in missing_in_db:
                await self._recorder.record(
                    exchange_orders[key], is_conditional=key[1]
                )
                report.synced.append(_label(key))

        report.balanced = not report.unresolved
        if report.missing_on_exchange or report.missing_in_db:
            logger.warning("reconcile.divergence", extra=report.as_dict())
        return report

    async def _sync_final_status(
        self, key: _OrderKey, *, symbol: str, market_type: MarketType
    ) -> bool:
        assert self._recorder is not None
        order_id, is_conditional = key
        try:
            if is_conditional:
                order = await self._exchange.orders.get_conditional_order(symbol, order_id)
            else:
                order = await self._exchange.orders.get_order(symbol, order_id, market_type)
        except Exception:
            logger.exception(
                "reconcile.fetch_final_status_failed",
                extra={"order": _label(key), "symbol": symbol},
            )
            return False
        await self._recorder.record(order, is_conditional=is_conditional)
        if order.is_open:
            # Ainda aberta: a listagem da exchange falhou ou atrasou (o listing
            # de algo orders devolve [] em erro). Não é divergência real.
            logger.info("reconcile.still_open", extra={"order": _label(key)})
        return True

    async def _load_db_open_orders(
        self,
        *,
        symbol: str | None,
        market_type: MarketType,
        include_conditional: bool,
    ) -> dict[_OrderKey, str]:
        """Chave → símbolo das ordens abertas gravadas."""
        stmt = select(OrderORM).where(
            OrderORM.status.in_(("NEW", "PARTIALLY_FILLED")),
            OrderORM.market_type == market_type.value,
        )
        if symbol is not None:
            stmt = stmt.where(OrderORM.symbol == symbol)
        if not include_conditional:
            stmt = stmt.where(OrderORM.is_conditional.is_(False))

        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return {(row.exchange_order_id, bool(row.is_conditional)): row.symbol for row in rows}

    async def _load_exchange_open_orders(
        self, *, symbol: str | None, market_type: MarketType
    ) -> tuple[dict[_OrderKey, Order], bool]:
        """Ordens abertas na exchange e se as condicionais foram consultadas.

        Sem suporte a condicionais (SPOT, ou provider sem a API), elas ficam
        fora da comparação dos dois lados para não gerar falsa divergência.
        """
        orders = await self._exchange.orders.list_open_orders(symbol, market_type)
        out: dict[_OrderKey, Order] = {(o.exchange_order_id, False): o for o in orders}
        if market_type is not MarketType.FUTURES:
            return out, False
        try:
            conditional = await self._exchange.orders.list_open_conditional_orders(symbol)
        except NotImplementedError:
            return out, False
        out.update({(o.exchange_order_id, True): o for o in conditional})
        return out, True
