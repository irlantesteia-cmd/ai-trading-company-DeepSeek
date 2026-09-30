from __future__ import annotations

import logging

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole
from app.events.event import (
    AgentStarted,
    AgentStopped,
    Event,
    OrderFilled,
    OrderIntentCreated,
    OrderSubmitted,
    SignalGenerated,
    SignalRejected,
)

logger = logging.getLogger(__name__)


class AuditorAgent(BaseAgent):
    """Escuta e registra eventos operacionais para trilha de auditoria.

    Persistência em DB entra na Fase 7; por ora, apenas log estruturado.
    """

    role = AgentRole.AUDITOR
    name = "auditor"

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {
            SignalGenerated: self._log,
            SignalRejected: self._log,
            OrderIntentCreated: self._log,
            OrderSubmitted: self._log,
            OrderFilled: self._log,
            AgentStarted: self._log,
            AgentStopped: self._log,
        }

    async def _log(self, event: Event) -> None:
        logger.info(
            "audit",
            extra={
                "event": event.name,
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "payload": event.model_dump(mode="json"),
            },
        )