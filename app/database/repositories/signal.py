from __future__ import annotations

from decimal import Decimal
from typing import cast

from sqlalchemy import CursorResult, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database.models.signal import SignalORM
from app.database.repositories.base import BaseRepository
from app.domain.models.signal import Signal


class SignalRepository(BaseRepository[SignalORM]):
    model = SignalORM

    async def insert(
        self,
        signal: Signal,
        *,
        decision: str | None = None,
        decision_reason: str | None = None,
        approved_quantity: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> None:
        """Grava o sinal. Idempotente por `signal_id` (reentrega não duplica)."""
        stmt = (
            pg_insert(SignalORM)
            .values(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                market_type=signal.market_type.value,
                direction=signal.direction.value,
                confidence=signal.confidence,
                horizon=signal.horizon,
                suggested_entry=signal.suggested_entry,
                suggested_stop=signal.suggested_stop,
                suggested_target=signal.suggested_target,
                regime=signal.regime.value,
                strategy=signal.strategy,
                agent=signal.agent,
                rationale=signal.rationale,
                generated_at=signal.generated_at,
                decision=decision,
                decision_reason=decision_reason,
                approved_quantity=approved_quantity,
                client_order_id=client_order_id,
            )
            .on_conflict_do_nothing(index_elements=[SignalORM.signal_id])
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def update_fields(self, signal_id: str, **fields: object) -> bool:
        """Atualiza decisão/ordem do sinal. False se o sinal ainda não existe."""
        stmt = (
            update(SignalORM)
            .where(SignalORM.signal_id == signal_id)
            .values(**fields, updated_at=func.now())
        )
        result = cast(CursorResult, await self.session.execute(stmt))
        await self.session.flush()
        return bool(result.rowcount)
