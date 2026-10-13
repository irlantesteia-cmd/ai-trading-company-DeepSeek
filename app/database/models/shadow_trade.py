from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class ShadowTradeORM(Base, TimestampMixin):
    """Trade hipotético do modo sombra (Fase 6): nenhuma ordem foi enviada.

    Uma linha por (hipótese, ativo, intervalo, entrada). A simulação é refeita
    a cada vela, então a posição aberta (OPEN, marcada no último fechamento)
    é atualizada na mesma linha até fechar (CLOSED).
    """

    __tablename__ = "shadow_trades"
    __table_args__ = (
        UniqueConstraint("hypothesis", "symbol", "interval", "entry_time", name="uq_shadow_trades_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8))  # OPEN | CLOSED
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    stop_price: Mapped[Decimal] = mapped_column(Numeric(28, 12))  # stop móvel atual / final
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    pnl: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    fees: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    funding: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    return_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    gross_return_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    funding_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    bars_held: Mapped[int] = mapped_column(Integer)
    regime_trend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    regime_vol: Mapped[str | None] = mapped_column(String(16), nullable=True)
