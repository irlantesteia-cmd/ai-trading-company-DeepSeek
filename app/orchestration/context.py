from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.events.bus import EventBus
from app.exchanges.base.exchange import Exchange

if TYPE_CHECKING:
    from app.orchestration.registry import AgentRegistry


@dataclass
class AgentContext:
    """Recursos compartilhados por todos os agentes.

    Não é congelado — `metadata` permite estado efêmero entre agentes
    (ex.: contadores, caches, correlação de pipeline).
    """

    exchange: Exchange
    event_bus: EventBus
    settings: Settings
    registry: AgentRegistry
    session_factory: async_sessionmaker | None = None
    metadata: dict[str, Any] = field(default_factory=dict)