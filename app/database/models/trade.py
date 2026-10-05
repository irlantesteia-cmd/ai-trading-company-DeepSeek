from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class TradeORM(Base, TimestampMixin):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market_type: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    position_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    fee_asset: Mapped[str] = mapped_column(String(16))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Ligação ao ciclo (entrada + saída). Linhas antigas ficam NULL.
    round_trip_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="UNKNOWN"
    )