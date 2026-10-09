from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class OrderORM(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange_order_id: Mapped[str] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market_type: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    executed_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), default=0)
    price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    average_price: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    time_in_force: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reduce_only: Mapped[bool] = mapped_column(default=False)
    position_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    exchange_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Ordem algo (SL/TP): `exchange_order_id` é o algoId.
    is_conditional: Mapped[bool] = mapped_column(default=False)

    fills: Mapped[list["OrderFillORM"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )


class OrderFillORM(Base, TimestampMixin):
    __tablename__ = "order_fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    commission: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    commission_asset: Mapped[str] = mapped_column(String(16))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    order: Mapped[OrderORM] = relationship(back_populates="fills")