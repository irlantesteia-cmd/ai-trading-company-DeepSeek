from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.dialects import postgresql

from app.core.enums import MarketType, OrderSide, OrderStatus, OrderType
from app.database.repositories.order import OrderRepository
from app.domain.models.order import Order


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)

    async def flush(self) -> None:
        pass


def _order() -> Order:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    return Order(
        exchange_order_id="999",
        client_order_id="ai-1",
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.FILLED,
        quantity=Decimal(1),
        executed_quantity=Decimal(1),
        created_at=now,
        updated_at=now,
    )


async def test_upsert_is_keyed_by_client_order_id_and_never_regresses():
    session = _CapturingSession()
    await OrderRepository(session).upsert(_order(), is_conditional=True)  # type: ignore[arg-type]

    stmt = session.statements[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    params = stmt.compile(dialect=postgresql.dialect()).params

    assert "ON CONFLICT (client_order_id) DO UPDATE" in sql
    # Estado final não regride; quantidade executada não diminui.
    assert "WHERE (orders.status NOT IN" in sql
    assert "greatest(orders.executed_quantity, excluded.executed_quantity)" in sql
    assert "updated_at = now()" in sql
    assert params["is_conditional"] is True
    assert params["status"] == "FILLED"
