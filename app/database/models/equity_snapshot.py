from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class EquitySnapshotORM(Base, TimestampMixin):
    """Foto do capital da conta futures (Fase 5c), a cada reconciliação.

    `total_equity` = saldo da carteira + P&L não realizado: é a curva sobre a
    qual o limite de drawdown a partir do pico é medido.
    """

    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    wallet_balance: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    total_equity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
