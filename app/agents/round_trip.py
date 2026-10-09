"""Mantém a tabela `round_trips` (ciclos entrada+saída).

Ouve `OrderFilled`. Para cada novo fill (dedup contra `trades`):
- Sem ciclo OPEN para o símbolo → abre ciclo (ENTRY).
- Ciclo OPEN, fill do mesmo lado da posição → scale-in (ENTRY).
- Ciclo OPEN, fill do lado oposto → exit. Fecha quando `exit_qty >= entry_qty`.
- Publica `RoundTripAssigned(round_trip_id, order_id, role)`.

Adicionalmente, quando um SL/TP dispara (role=EXIT com client_order_id
começando em `sl-`/`tp-`), cancela as demais ordens condicionais do símbolo
— evita órfãs quando um lado da proteção executa.

Ordem de registro em `run_bot.py`: **antes** do `TradeRecorderAgent`.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.agents.base import BaseAgent, EventHandler
from app.core.config import settings
from app.core.enums import (
    AgentRole,
    CloseReason,
    PositionSide,
    RoundTripStatus,
    TradeRole,
)
from app.database.models.round_trip import RoundTripORM
from app.database.models.trade import TradeORM
from app.domain.models.order import Order, OrderFill
from app.events.event import Event, OrderFilled, RoundTripAssigned

logger = logging.getLogger(__name__)

_QUANTITY_TOLERANCE = Decimal("0.00000001")


class RoundTripAgent(BaseAgent):
    role = AgentRole.AUDITOR
    name = "round_trip"

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {OrderFilled: self._on_order_filled}

    # ------------------------------------------------------------ event bus
    async def _on_order_filled(self, event: Event) -> None:
        if not isinstance(event, OrderFilled):
            return

        order = event.order
        if order is None or not order.fills:
            return
        if self.context.session_factory is None:
            return

        trade_ids = [
            self._trade_id(order, fill, i) for i, fill in enumerate(order.fills)
        ]

        async with self.context.session_factory() as session:
            existing = await session.execute(
                select(TradeORM.trade_id).where(TradeORM.trade_id.in_(trade_ids))
            )
            existing_ids = {row for (row,) in existing}
            new_fills = [
                fill
                for i, fill in enumerate(order.fills)
                if trade_ids[i] not in existing_ids
            ]
            if not new_fills:
                logger.debug(
                    "round_trip.skip_all_duplicates",
                    extra={"order_id": order.exchange_order_id},
                )
                return

            trip = await self._get_open_trip(session, order.symbol)

            if trip is None:
                trip = self._new_trip(order, new_fills)
                session.add(trip)
                role = TradeRole.ENTRY
            elif self._is_same_side(trip.position_side, order.side.value):
                self._apply_scale_in(trip, new_fills)
                role = TradeRole.ENTRY
            else:
                self._apply_exit(trip, new_fills)
                role = TradeRole.EXIT
                if self._is_fully_closed(trip):
                    self._close_trip(trip, order)

            await session.commit()
            round_trip_id = trip.round_trip_id

        await self.context.event_bus.publish(
            RoundTripAssigned(
                round_trip_id=round_trip_id,
                order_id=order.exchange_order_id,
                symbol=order.symbol,
                role=role.value,
            )
        )

        # Se um SL/TP disparou, cancelar o(s) irmão(s) pendente(s) do símbolo.
        await self._maybe_cancel_siblings(order, role.value)

    # ---------------------------------------------------------------- dedup
    @staticmethod
    def _trade_id(order: Order, fill: OrderFill, index: int) -> str:
        if fill.trade_id:
            return f"{order.exchange_order_id}-{fill.trade_id}"
        return f"{order.exchange_order_id}-{index}"

    # --------------------------------------------------------------- lookup
    @staticmethod
    async def _get_open_trip(session, symbol: str) -> RoundTripORM | None:
        stmt = (
            select(RoundTripORM)
            .where(
                RoundTripORM.symbol == symbol,
                RoundTripORM.status == RoundTripStatus.OPEN.value,
            )
            .order_by(RoundTripORM.opened_at.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalars().first()

    # -------------------------------------------------------------- lifecycle
    @staticmethod
    def _new_trip(order: Order, fills: list[OrderFill]) -> RoundTripORM:
        qty = sum((f.quantity for f in fills), Decimal(0))
        notional = sum((f.quantity * f.price for f in fills), Decimal(0))
        avg = notional / qty if qty > 0 else Decimal(0)
        fee = sum((f.commission for f in fills), Decimal(0))
        ts = fills[0].timestamp
        position_side = (
            PositionSide.LONG if order.side.value == "BUY" else PositionSide.SHORT
        )
        return RoundTripORM(
            round_trip_id=str(uuid4()),
            symbol=order.symbol,
            market_type=order.market_type.value,
            position_side=position_side.value,
            status=RoundTripStatus.OPEN.value,
            entry_quantity=qty,
            entry_avg_price=avg,
            entry_fee=fee,
            exit_quantity=Decimal(0),
            exit_fee=Decimal(0),
            opened_at=ts,
        )

    @staticmethod
    def _apply_scale_in(trip: RoundTripORM, fills: list[OrderFill]) -> None:
        add_qty = sum((f.quantity for f in fills), Decimal(0))
        add_notional = sum((f.quantity * f.price for f in fills), Decimal(0))
        add_fee = sum((f.commission for f in fills), Decimal(0))
        new_qty = trip.entry_quantity + add_qty
        if new_qty > 0:
            trip.entry_avg_price = (
                trip.entry_quantity * trip.entry_avg_price + add_notional
            ) / new_qty
        trip.entry_quantity = new_qty
        trip.entry_fee = (trip.entry_fee or Decimal(0)) + add_fee

    @staticmethod
    def _apply_exit(trip: RoundTripORM, fills: list[OrderFill]) -> None:
        add_qty = sum((f.quantity for f in fills), Decimal(0))
        add_notional = sum((f.quantity * f.price for f in fills), Decimal(0))
        add_fee = sum((f.commission for f in fills), Decimal(0))
        new_qty = trip.exit_quantity + add_qty
        current_avg = trip.exit_avg_price or Decimal(0)
        if new_qty > 0:
            trip.exit_avg_price = (
                trip.exit_quantity * current_avg + add_notional
            ) / new_qty
        trip.exit_quantity = new_qty
        trip.exit_fee = (trip.exit_fee or Decimal(0)) + add_fee

    @staticmethod
    def _is_fully_closed(trip: RoundTripORM) -> bool:
        return trip.exit_quantity + _QUANTITY_TOLERANCE >= trip.entry_quantity

    @staticmethod
    def _close_trip(trip: RoundTripORM, order: Order) -> None:
        entry = trip.entry_avg_price
        exit_ = trip.exit_avg_price or Decimal(0)
        qty = min(trip.entry_quantity, trip.exit_quantity)
        if trip.position_side == PositionSide.LONG.value:
            gross = (exit_ - entry) * qty
        else:
            gross = (entry - exit_) * qty
        net = gross - (trip.entry_fee or Decimal(0)) - (trip.exit_fee or Decimal(0))

        trip.status = RoundTripStatus.CLOSED.value
        trip.closed_at = order.updated_at
        trip.close_reason = RoundTripAgent._reason_from_client_order_id(
            order.client_order_id
        ).value
        trip.gross_pnl = gross
        trip.net_pnl = net

    @staticmethod
    def _reason_from_client_order_id(client_order_id: str) -> CloseReason:
        return CloseReason.from_client_order_id(client_order_id)

    @staticmethod
    def _is_same_side(position_side: str, order_side: str) -> bool:
        if position_side == PositionSide.LONG.value:
            return order_side == "BUY"
        if position_side == PositionSide.SHORT.value:
            return order_side == "SELL"
        return False

    # --------------------------------------------------------------- siblings
    @staticmethod
    def _should_cancel_siblings(order: Order, role: str) -> bool:
        """Só cancela irmãos se o fill foi um EXIT vindo de SL/TP condicional."""
        if role != TradeRole.EXIT.value:
            return False
        cid = order.client_order_id or ""
        return cid.startswith(("sl-", "tp-"))

    async def _maybe_cancel_siblings(self, order: Order, role: str) -> None:
        """Cancela as demais ordens condicionais do símbolo quando um SL/TP dispara.

        Best-effort: qualquer falha é logada e o caller segue — o fill do SL/TP
        já foi processado e não pode ser revertido. Sem isso, o irmão (a outra
        ponta da proteção) ficaria pendurado até o próximo close/reconcile.
        """
        if not settings.cancel_sibling_on_protective_fill:
            return
        if not self._should_cancel_siblings(order, role):
            return

        kind = (
            CloseReason.STOP_LOSS.value
            if (order.client_order_id or "").startswith("sl-")
            else CloseReason.TAKE_PROFIT.value
        )

        try:
            canceled = await self.context.exchange.orders.cancel_all_algo_orders(
                order.symbol
            )
            logger.info(
                "round_trip.sibling_canceled",
                extra={
                    "symbol": order.symbol,
                    "triggered_order_id": order.exchange_order_id,
                    "client_order_id": order.client_order_id,
                    "kind": kind,
                    "count": canceled,
                },
            )
        except Exception:
            logger.exception(
                "round_trip.sibling_cancel_failed",
                extra={
                    "symbol": order.symbol,
                    "triggered_order_id": order.exchange_order_id,
                    "client_order_id": order.client_order_id,
                    "kind": kind,
                },
            )