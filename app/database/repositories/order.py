from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.enums import OrderStatus
from app.database.models.order import OrderORM
from app.database.repositories.base import BaseRepository
from app.domain.models.order import Order

# Estados finais: uma atualização atrasada (ex.: evento NEW do UDS chegando
# depois do FILLED vindo do polling REST) não pode fazer a ordem regredir.
TERMINAL_STATUSES: tuple[str, ...] = (
    OrderStatus.FILLED.value,
    OrderStatus.CANCELED.value,
    OrderStatus.REJECTED.value,
    OrderStatus.EXPIRED.value,
)


class OrderRepository(BaseRepository[OrderORM]):
    model = OrderORM

    async def upsert(self, order: Order, *, is_conditional: bool = False) -> None:
        """Insere ou atualiza a ordem pela chave `client_order_id`.

        No conflito, só atualiza enquanto a ordem gravada não estiver num
        estado final, e `executed_quantity` nunca diminui.
        """
        row = {
            "exchange_order_id": order.exchange_order_id,
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "market_type": order.market_type.value,
            "side": order.side.value,
            "type": order.type.value,
            "status": order.status.value,
            "quantity": order.quantity,
            "executed_quantity": order.executed_quantity,
            "price": order.price,
            "average_price": order.average_price,
            "stop_price": order.stop_price,
            "time_in_force": order.time_in_force.value if order.time_in_force else None,
            "reduce_only": order.reduce_only,
            "position_side": order.position_side.value if order.position_side else None,
            "exchange_created_at": order.created_at,
            "is_conditional": is_conditional,
        }
        stmt = pg_insert(OrderORM).values(row)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=[OrderORM.client_order_id],
            set_={
                # Ordem rejeitada antes de chegar à exchange é gravada com id
                # vazio; não sobrescrever um id real com vazio.
                "exchange_order_id": func.coalesce(
                    func.nullif(excluded.exchange_order_id, ""),
                    OrderORM.exchange_order_id,
                ),
                "status": excluded.status,
                "executed_quantity": func.greatest(
                    OrderORM.executed_quantity, excluded.executed_quantity
                ),
                "average_price": func.coalesce(
                    excluded.average_price, OrderORM.average_price
                ),
                "stop_price": func.coalesce(excluded.stop_price, OrderORM.stop_price),
                "updated_at": func.now(),
            },
            where=OrderORM.status.notin_(TERMINAL_STATUSES),
        )
        await self.session.execute(stmt)
        await self.session.flush()
