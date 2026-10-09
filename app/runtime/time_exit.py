"""Saída por tempo: fecha posições abertas há mais que o limite configurado.

Sem ela, uma posição cujo preço oscila entre o SL e o TP fica aberta
indefinidamente (teste ao vivo de 2026-10-09: mais de 1 h num sinal de 5m).

A cada ciclo, para cada (símbolo, lado), usa o round trip `OPEN` **mais
recente** do banco: um registro antigo que tenha ficado aberto por engano
(saída não registrada) não fecha uma posição nova. Só fecha se a posição
existir na exchange. O fechamento usa o prefixo `tx-`, que o
`RoundTripAgent` grava como `close_reason=TIME_EXIT`; o `close_position`
cancela antes o SL/TP pendente.

Falhas são logadas e isoladas por posição: a tarefa nunca derruba o bot.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import PositionSide, RoundTripStatus
from app.database.models.round_trip import RoundTripORM
from app.events.bus import EventBus
from app.events.event import OrderFilled
from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)

TIME_EXIT_PREFIX = "tx"


class TimeExitMonitor:
    def __init__(
        self,
        *,
        exchange: Exchange,
        event_bus: EventBus,
        session_factory: async_sessionmaker,
        max_holding_minutes: float,
        check_interval_s: float = 60.0,
    ) -> None:
        if max_holding_minutes <= 0:
            raise ValueError("max_holding_minutes deve ser > 0")
        self._exchange = exchange
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._max_holding = timedelta(minutes=max_holding_minutes)
        self._check_interval_s = check_interval_s

    async def check_once(self, *, now: datetime | None = None) -> int:
        """Um ciclo. Retorna quantas posições foram fechadas."""
        now = now or datetime.now(UTC)
        closed = 0
        for trip in await self._newest_open_trips():
            held = now - trip.opened_at
            if held < self._max_holding:
                continue
            if await self._close(trip, held):
                closed += 1
        return closed

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                logger.exception("time_exit.check_failed")
            await asyncio.sleep(self._check_interval_s)

    async def _newest_open_trips(self) -> list[RoundTripORM]:
        stmt = (
            select(RoundTripORM)
            .where(RoundTripORM.status == RoundTripStatus.OPEN.value)
            .order_by(RoundTripORM.opened_at.desc())
        )
        async with self._session_factory() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        newest: dict[tuple[str, str], RoundTripORM] = {}
        for trip in rows:  # ordem decrescente: o primeiro de cada chave é o mais recente
            newest.setdefault((trip.symbol, trip.position_side), trip)
        return list(newest.values())

    async def _close(self, trip: RoundTripORM, held: timedelta) -> bool:
        extra = {
            "symbol": trip.symbol,
            "position_side": trip.position_side,
            "round_trip_id": trip.round_trip_id,
            "held_minutes": round(held.total_seconds() / 60, 1),
        }
        try:
            order = await self._exchange.positions.close_position(
                trip.symbol,
                PositionSide(trip.position_side),
                client_order_prefix=TIME_EXIT_PREFIX,
            )
        except Exception:
            logger.exception("time_exit.close_failed", extra=extra)
            return False
        if order is None:
            # Sem posição na exchange: o registro OPEN está desatualizado.
            logger.warning("time_exit.no_position", extra=extra)
            return False

        logger.info("time_exit.closed", extra={**extra, "order_id": order.exchange_order_id})
        if order.fills and order.average_price is not None:
            await self._event_bus.publish(
                OrderFilled(
                    exchange_order_id=order.exchange_order_id,
                    symbol=order.symbol,
                    filled_quantity=float(order.executed_quantity),
                    average_price=float(order.average_price),
                    order=order,
                )
            )
        return True
