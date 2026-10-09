from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class RoundTripORM(Base, TimestampMixin):
    """Ciclo completo: entrada(s) + saída(s) de uma posição.

    Uma linha por "trade" lógico. Aberta no primeiro fill de entrada,
    fechada quando `exit_quantity >= entry_quantity`.
    """

    __tablename__ = "round_trips"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_trip_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market_type: Mapped[str] = mapped_column(String(16))
    position_side: Mapped[str] = mapped_column(String(8))  # LONG / SHORT
    status: Mapped[str] = mapped_column(String(16), index=True)  # OPEN / CLOSED
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    entry_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    entry_avg_price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    entry_fee: Mapped[Decimal] = mapped_column(
        Numeric(28, 12), default=Decimal(0), server_default="0"
    )

    exit_quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 12), default=Decimal(0), server_default="0"
    )
    exit_avg_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    exit_fee: Mapped[Decimal] = mapped_column(
        Numeric(28, 12), default=Decimal(0), server_default="0"
    )

    gross_pnl: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)