from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class MarketMetricORM(Base, TimestampMixin):
    """Métricas de derivativos por ativo em blocos de 5 min (coletor L5b).

    Uma linha por (symbol, ts). Fontes diferentes preenchem colunas
    diferentes da mesma linha: o upsert só sobrescreve com valores não nulos.
    """

    __tablename__ = "market_metrics"
    __table_args__ = (UniqueConstraint("symbol", "ts", name="uq_market_metrics_symbol_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Histórico de 5 min (/futures/data/*, até 30 dias para trás)
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    open_interest_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    global_ls_ratio: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    top_ls_position_ratio: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    taker_buy_sell_ratio: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    taker_buy_volume: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    taker_sell_volume: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    # Fotos tiradas na coleta (sem histórico na Binance)
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    index_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    book_bid_notional: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    book_ask_notional: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    book_imbalance: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    spread_bps: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
