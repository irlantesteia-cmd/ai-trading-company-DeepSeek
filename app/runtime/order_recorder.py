"""Persistência do ciclo de vida das ordens na tabela `orders`.

`RecordingOrderProvider` envolve o `OrderProvider` da exchange e grava cada
ordem colocada, cancelada ou consultada. É aplicado na montagem do adapter
(`BinanceAdapter(orders_wrapper=...)`), então cobre todos os caminhos:
entrada (`ExecutionAgent`), SL/TP (`place_conditional_order`) e fechamento
(`positions.close_position`).

Regra de isolamento: falha ao gravar é logada e **nunca** interrompe a
operação de trading.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import MarketType, OrderStatus
from app.database.repositories.order import OrderRepository
from app.domain.models.order import Order, OrderRequest
from app.exchanges.base.orders import OrderProvider

logger = logging.getLogger(__name__)


class OrderRecorder:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def record(self, order: Order, *, is_conditional: bool = False) -> None:
        if not order.client_order_id:
            # Sem chave de upsert (ex.: ordem criada fora do bot sem clientId).
            logger.warning(
                "order_recorder.missing_client_order_id",
                extra={"exchange_order_id": order.exchange_order_id},
            )
            return
        try:
            async with self._session_factory() as session:
                await OrderRepository(session).upsert(order, is_conditional=is_conditional)
                await session.commit()
        except Exception:
            logger.exception(
                "order_recorder.persist_failed",
                extra={
                    "client_order_id": order.client_order_id,
                    "status": order.status.value,
                },
            )

    async def record_rejected(
        self, request: OrderRequest, *, is_conditional: bool = False
    ) -> None:
        """Ordem recusada pela exchange (exceção no envio): grava como REJECTED."""
        now = datetime.now(UTC)
        await self.record(
            Order(
                exchange_order_id="",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                market_type=request.market_type,
                side=request.side,
                type=request.type,
                status=OrderStatus.REJECTED,
                quantity=request.quantity,
                executed_quantity=Decimal(0),
                price=request.price,
                stop_price=request.stop_price,
                time_in_force=request.time_in_force,
                reduce_only=request.reduce_only,
                position_side=request.position_side,
                created_at=now,
                updated_at=now,
            ),
            is_conditional=is_conditional,
        )


class RecordingOrderProvider(OrderProvider):
    """Decorator de `OrderProvider` que grava cada resultado em `orders`."""

    def __init__(self, inner: OrderProvider, recorder: OrderRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    @property
    def inner(self) -> OrderProvider:
        return self._inner

    async def place_order(self, request: OrderRequest) -> Order:
        try:
            order = await self._inner.place_order(request)
        except Exception:
            await self._recorder.record_rejected(request)
            raise
        await self._recorder.record(order)
        return order

    async def place_conditional_order(self, request: OrderRequest) -> Order:
        try:
            order = await self._inner.place_conditional_order(request)
        except NotImplementedError:
            raise
        except Exception:
            await self._recorder.record_rejected(request, is_conditional=True)
            raise
        await self._recorder.record(order, is_conditional=True)
        return order

    async def cancel_order(
        self, symbol: str, exchange_order_id: str, market_type: MarketType
    ) -> Order:
        order = await self._inner.cancel_order(symbol, exchange_order_id, market_type)
        await self._recorder.record(order)
        return order

    async def get_order(
        self, symbol: str, exchange_order_id: str, market_type: MarketType
    ) -> Order:
        order = await self._inner.get_order(symbol, exchange_order_id, market_type)
        await self._recorder.record(order)
        return order

    async def list_open_orders(
        self, symbol: str | None, market_type: MarketType
    ) -> list[Order]:
        return await self._inner.list_open_orders(symbol, market_type)

    async def cancel_all_algo_orders(self, symbol: str) -> int:
        return await self._inner.cancel_all_algo_orders(symbol)

    async def list_open_conditional_orders(self, symbol: str | None) -> list[Order]:
        return await self._inner.list_open_conditional_orders(symbol)

    async def get_conditional_order(self, symbol: str, exchange_order_id: str) -> Order:
        order = await self._inner.get_conditional_order(symbol, exchange_order_id)
        await self._recorder.record(order, is_conditional=True)
        return order

    def __getattr__(self, name: str) -> Any:
        # Métodos específicos do provider (ex.: `list_open_algo_orders`).
        if name == "_inner":  # ainda não inicializado: evita recursão
            raise AttributeError(name)
        return getattr(self._inner, name)
