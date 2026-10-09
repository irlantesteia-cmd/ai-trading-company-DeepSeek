from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class ReconciliationRunORM(Base, TimestampMixin):
    """Uma execução do `Reconciler` (ordens abertas no DB × exchange)."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    market_type: Mapped[str] = mapped_column(String(16))
    db_open: Mapped[int] = mapped_column()
    exchange_open: Mapped[int] = mapped_column()
    missing_on_exchange: Mapped[list[str]] = mapped_column(JSONB)
    missing_in_db: Mapped[list[str]] = mapped_column(JSONB)
    synced: Mapped[list[str]] = mapped_column(JSONB)
    unresolved: Mapped[list[str]] = mapped_column(JSONB)
    balanced: Mapped[bool] = mapped_column(index=True)
