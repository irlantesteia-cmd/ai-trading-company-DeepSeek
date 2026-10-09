"""SignalRecorderAgent — persiste cada sinal e a decisão tomada sobre ele.

Ouve:
- `SignalGenerated` → grava o sinal em `signals` (IGNORED se a
  auto-execução estiver desligada: o TradingManager não publica nada);
- `SignalApproved` / `SignalRejected` → grava a decisão;
- `OrderIntentCreated` → grava o `client_order_id` da ordem gerada
  (`client_order_id_for_intent`, o mesmo que o `ExecutionAgent` usa).

Ordem dos eventos: o `EventBus` chama os handlers em sequência, e o
TradingManager (inscrito antes) publica a decisão *dentro* do seu handler
de `SignalGenerated`, ou seja, antes de este agente inserir o sinal. Por isso
atualizações para um sinal ainda não gravado ficam pendentes em memória e
são aplicadas no insert.

Falhas de banco são logadas e nunca propagam: persistência não pode
interferir no trading.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from decimal import Decimal

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole
from app.database.repositories.signal import SignalRepository
from app.domain.models.order_intent import client_order_id_for_intent
from app.events.event import (
    Event,
    OrderIntentCreated,
    SignalApproved,
    SignalGenerated,
    SignalRejected,
)

logger = logging.getLogger(__name__)

APPROVED = "APPROVED"
REJECTED = "REJECTED"
IGNORED = "IGNORED"

# Limite de sinais com atualização pendente (sinal nunca gravado não pode
# vazar memória para sempre).
_MAX_PENDING = 1000


class SignalRecorderAgent(BaseAgent):
    role = AgentRole.AUDITOR
    name = "signal_recorder"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._pending: OrderedDict[str, dict[str, object]] = OrderedDict()

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {
            SignalGenerated: self._on_signal_generated,
            SignalApproved: self._on_signal_approved,
            SignalRejected: self._on_signal_rejected,
            OrderIntentCreated: self._on_order_intent,
        }

    # ------------------------------------------------------------ handlers
    async def _on_signal_generated(self, event: Event) -> None:
        if not isinstance(event, SignalGenerated):
            return
        if event.signal is None:
            logger.warning(
                "signal_recorder.payload_missing", extra={"signal_id": event.signal_id}
            )
            return
        fields = self._pending.pop(event.signal_id, {})
        if "decision" not in fields and not self.context.settings.signal_auto_execution_enabled:
            fields["decision"] = IGNORED
            fields["decision_reason"] = "SIGNAL_AUTO_EXECUTION_ENABLED=false"
        session_factory = self.context.session_factory
        if session_factory is None:
            return
        try:
            async with session_factory() as session:
                await SignalRepository(session).insert(event.signal, **fields)  # type: ignore[arg-type]
                await session.commit()
        except Exception:
            logger.exception(
                "signal_recorder.persist_failed", extra={"signal_id": event.signal_id}
            )

    async def _on_signal_approved(self, event: Event) -> None:
        if isinstance(event, SignalApproved):
            await self._update(
                event.signal_id,
                decision=APPROVED,
                approved_quantity=Decimal(str(event.approved_quantity)),
            )

    async def _on_signal_rejected(self, event: Event) -> None:
        if isinstance(event, SignalRejected):
            await self._update(event.signal_id, decision=REJECTED, decision_reason=event.reason)

    async def _on_order_intent(self, event: Event) -> None:
        if isinstance(event, OrderIntentCreated):
            await self._update(
                event.signal_id, client_order_id=client_order_id_for_intent(event.intent_id)
            )

    # ------------------------------------------------------------- helpers
    async def _update(self, signal_id: str, **fields: object) -> None:
        session_factory = self.context.session_factory
        if session_factory is None:
            return
        try:
            async with session_factory() as session:
                updated = await SignalRepository(session).update_fields(signal_id, **fields)
                await session.commit()
        except Exception:
            logger.exception("signal_recorder.update_failed", extra={"signal_id": signal_id})
            return
        if not updated:
            # Sinal ainda não gravado (a decisão chegou antes do insert).
            self._pending.setdefault(signal_id, {}).update(fields)
            self._pending.move_to_end(signal_id)
            while len(self._pending) > _MAX_PENDING:
                self._pending.popitem(last=False)
