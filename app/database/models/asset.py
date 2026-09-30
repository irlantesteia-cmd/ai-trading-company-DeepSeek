from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class TradingPairORM(Base, TimestampMixin):
    __tablename__ = "trading_pairs"
    __table_args__ = (
        UniqueConstraint("symbol", "market_type", name="uq_pair_symbol_market"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    base: Mapped[str] = mapped_column(String(16))
    quote: Mapped[str] = mapped_column(String(16))
    market_type: Mapped[str] = mapped_column(String(16))
    price_precision: Mapped[int] = mapped_column()
    quantity_precision: Mapped[int] = mapped_column()
    min_notional: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    step_size: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    status: Mapped[str] = mapped_column(String(16), default="TRADING")