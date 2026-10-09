from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Float, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class SignalORM(Base, TimestampMixin):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market_type: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[float] = mapped_column(Float)
    horizon: Mapped[str] = mapped_column(String(8))
    suggested_entry: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    suggested_stop: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    suggested_target: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    regime: Mapped[str] = mapped_column(String(16))
    strategy: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(64), index=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Decisão do TradingManager: APPROVED / REJECTED / IGNORED (None = pendente).
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    # Ordem gerada pelo sinal (junção com `orders.client_order_id`).
    client_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
