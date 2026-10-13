from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class ShadowRunORM(Base, TimestampMixin):
    """Um ciclo do serviço do modo sombra (Fase 6b): saúde e resumo.

    Gravado a cada ciclo, mesmo sem trade novo, para o alerta de serviço parado.
    """

    __tablename__ = "shadow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis: Mapped[str] = mapped_column(String(64), index=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # REGISTERED = rodou; NOT_REGISTERED = hipótese sem T0 no registro;
    # INVALID = registro inválido (ver `error`).
    status: Mapped[str] = mapped_column(String(16))
    start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_candle_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    streams: Mapped[int] = mapped_column(Integer, default=0)
    streams_failed: Mapped[int] = mapped_column(Integer, default=0)
    streams_stale: Mapped[int] = mapped_column(Integer, default=0)
    streams_short_warmup: Mapped[int] = mapped_column(Integer, default=0)
    closed_trades: Mapped[int] = mapped_column(Integer, default=0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
