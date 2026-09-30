from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class FuturesPositionSnapshotORM(Base, TimestampMixin):
    """Snapshot periódico de posições FUTURES (histórico para auditoria)."""

    __tablename__ = "futures_position_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    position_side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    mark_price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    leverage: Mapped[int] = mapped_column()
    margin_type: Mapped[str] = mapped_column(String(16))
    liquidation_price: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)