from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class CandleORM(Base, TimestampMixin):
    """Candle persistido para backtest e treino de modelos ML.

    Chave de unicidade: (symbol, market_type, interval, open_time).
    Reingestão é idempotente via `ON CONFLICT DO UPDATE` (OHLCV + taker_buy).
    """

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "market_type",
            "interval",
            "open_time",
            name="uq_candle_symbol_market_interval_time",
        ),
        Index(
            "ix_candle_lookup",
            "symbol",
            "market_type",
            "interval",
            "open_time",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market_type: Mapped[str] = mapped_column(String(16))
    interval: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    high: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    low: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    close: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    trades: Mapped[int] = mapped_column()
    taker_buy_base_volume: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )