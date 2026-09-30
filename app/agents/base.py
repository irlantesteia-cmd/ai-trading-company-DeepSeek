from __future__ import annotations

import logging
from abc import ABC
from collections.abc import Awaitable, Callable

from app.core.enums import AgentRole
from app.events.event import AgentStarted, AgentStopped, Event
from app.orchestration.context import AgentContext

logger = logging.getLogger(__name__)

EventHandler = Callable[[Event], Awaitable[None]]


class BaseAgent(ABC):
    """Contrato base de todo agente da empresa.

    Ciclo de vida:
        start() → subscreve eventos → on_start() → publica AgentStarted
        stop()  → dessubscreve   → on_stop()  → publica AgentStopped
    """

    role: AgentRole
    name: str
    enabled: bool = True

    def __init__(self, context: AgentContext) -> None:
        self.context = context
        self._started = False

    # ------------------------------------------------------------------ status
    @property
    def started(self) -> bool:
        return self._started

    # -------------------------------------------------------- subscriptions
    def subscriptions(self) -> dict[type[Event], EventHandler]:
        """Eventos que este agente quer ouvir. Default: nenhum."""
        return {}

    # -------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._started:
            return
        for event_type, handler in self.subscriptions().items():
            self.context.event_bus.subscribe(event_type, handler)
        await self.on_start()
        self._started = True
        await self.context.event_bus.publish(
            AgentStarted(agent_name=self.name, role=self.role.value)
        )
        logger.info(
            "agent.started",
            extra={"agent_name": self.name, "role": self.role.value},
        )

    async def stop(self) -> None:
        if not self._started:
            return
        for event_type, handler in self.subscriptions().items():
            self.context.event_bus.unsubscribe(event_type, handler)
        await self.on_stop()
        self._started = False
        await self.context.event_bus.publish(
            AgentStopped(agent_name=self.name, role=self.role.value)
        )
        logger.info(
            "agent.stopped",
            extra={"agent_name": self.name, "role": self.role.value},
        )

    async def on_start(self) -> None:
        """Hook opcional para setup."""

    async def on_stop(self) -> None:
        """Hook opcional para teardown."""