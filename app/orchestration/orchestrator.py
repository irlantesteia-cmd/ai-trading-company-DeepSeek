from __future__ import annotations

import logging

from app.core.enums import AgentRole
from app.domain.models.order import Order
from app.domain.models.signal import Signal
from app.orchestration.context import AgentContext
from app.orchestration.registry import AgentRegistry

logger = logging.getLogger(__name__)


class Orchestrator:
    """CEO / coordenador.

    Não contém lógica de trading — apenas orquestra agentes:
    - sobe/derruba tudo com start()/stop()
    - expõe fachada para pipelines (submit_signal)
    - publica eventos sistêmicos
    """

    def __init__(self, context: AgentContext, registry: AgentRegistry) -> None:
        self._context = context
        self._registry = registry
        self._running = False

    @property
    def context(self) -> AgentContext:
        return self._context

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        logger.info("orchestrator.starting", extra={"agents": self._registry.names()})
        await self._registry.start_all()
        self._running = True
        logger.info("orchestrator.started")

    async def stop(self) -> None:
        if not self._running:
            return
        logger.info("orchestrator.stopping")
        await self._registry.stop_all()
        self._running = False
        logger.info("orchestrator.stopped")

    async def submit_signal(self, signal: Signal) -> Order | None:
        """Delega ao TradingManager. Retorna a ordem criada, se houver."""
        manager = self._registry.require_one(AgentRole.TRADING_MANAGER)
        return await manager.process_signal(signal)  # type: ignore[attr-defined]