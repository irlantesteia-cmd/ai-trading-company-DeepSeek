"""Coletor de métricas operacionais para o EvolutionLoop.

Lê `round_trips` fechados dentro de uma janela de lookback e calcula as
métricas que o `EvolutionLoop` usa para decidir se vale propor uma mudança.

Fills crus ficam em `trades`; ciclos completos (entrada + saída) ficam em
`round_trips`. Só os ciclos fechados têm P&L realizado — daí a fonte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import RoundTripStatus
from app.database.models.round_trip import RoundTripORM

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradingMetrics:
    """Snapshot imutável de métricas de trading numa janela de tempo."""

    num_trades: int
    total_pnl: Decimal
    total_fees: Decimal
    win_count: int
    loss_count: int
    win_rate: float | None
    avg_win: Decimal
    avg_loss: Decimal
    lookback_hours: int
    computed_at: datetime

    def as_dict(self) -> dict[str, float]:
        return {
            "num_trades": float(self.num_trades),
            "total_pnl": float(self.total_pnl),
            "total_fees": float(self.total_fees),
            "win_rate": self.win_rate if self.win_rate is not None else 0.0,
            "avg_win": float(self.avg_win),
            "avg_loss": float(self.avg_loss),
        }


class MetricsCollector:
    """Coleta round trips fechados e calcula métricas operacionais.

    Nunca levanta exceção por dados ausentes — DB vazio retorna métricas
    zeradas com `win_rate=None`. Exceções do DB propagam e são tratadas
    pelo caller (o `EvolutionLoop` já captura).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        lookback_hours: int = 24,
    ) -> None:
        if lookback_hours <= 0:
            raise ValueError("lookback_hours deve ser > 0")
        self._session_factory = session_factory
        self._lookback_hours = lookback_hours

    @property
    def lookback_hours(self) -> int:
        return self._lookback_hours

    async def collect(self, *, now: datetime | None = None) -> TradingMetrics:
        now = now or datetime.now(UTC)
        since = now - timedelta(hours=self._lookback_hours)

        stmt = select(RoundTripORM).where(
            RoundTripORM.status == RoundTripStatus.CLOSED.value,
            RoundTripORM.closed_at.is_not(None),
            RoundTripORM.closed_at >= since,
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()

        total_pnl = Decimal(0)
        total_fees = Decimal(0)
        wins: list[Decimal] = []
        losses: list[Decimal] = []

        for row in rows:
            total_fees += (row.entry_fee or Decimal(0)) + (row.exit_fee or Decimal(0))
            pnl = row.net_pnl
            if pnl is None:
                continue
            total_pnl += pnl
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(pnl)

        decided = len(wins) + len(losses)
        win_rate = (len(wins) / decided) if decided > 0 else None
        avg_win = (sum(wins, Decimal(0)) / len(wins)) if wins else Decimal(0)
        avg_loss = (sum(losses, Decimal(0)) / len(losses)) if losses else Decimal(0)

        metrics = TradingMetrics(
            num_trades=len(rows),
            total_pnl=total_pnl,
            total_fees=total_fees,
            win_count=len(wins),
            loss_count=len(losses),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            lookback_hours=self._lookback_hours,
            computed_at=now,
        )
        logger.info(
            "metrics.collected",
            extra={
                "num_trades": metrics.num_trades,
                "win_rate": metrics.win_rate,
                "total_pnl": str(metrics.total_pnl),
                "lookback_hours": metrics.lookback_hours,
            },
        )
        return metrics