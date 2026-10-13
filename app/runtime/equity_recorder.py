"""Grava a foto do capital (Fase 5c) a cada ciclo de reconciliação.

Falha é logada e nunca derruba a reconciliação: sem fotos novas, o pico do
drawdown só fica mais antigo (conservador).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.equity_snapshot import EquitySnapshotORM
from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)


async def record_equity_snapshot(
    exchange: Exchange, session_factory: async_sessionmaker, *, now: datetime | None = None
) -> EquitySnapshotORM | None:
    try:
        balance = await exchange.account.get_futures_balance("USDT")
        positions = await exchange.account.get_futures_positions()
        unrealized = sum((p.unrealized_pnl for p in positions if p.is_open), Decimal(0))
        snapshot = EquitySnapshotORM(
            taken_at=now or datetime.now(UTC),
            wallet_balance=balance.total,
            unrealized_pnl=unrealized,
            total_equity=balance.total + unrealized,
        )
        async with session_factory() as session:
            session.add(snapshot)
            await session.commit()
    except Exception:
        logger.exception("equity_snapshot.failed")
        return None
    return snapshot
