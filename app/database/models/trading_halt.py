from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class TradingHaltORM(Base, TimestampMixin):
    """Trava de trading. Ativa enquanto `cleared_at` for NULL."""

    __tablename__ = "trading_halts"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    rule: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cleared_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clear_note: Mapped[str | None] = mapped_column(Text, nullable=True)
