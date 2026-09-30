from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.enums import AgentRole
from app.core.exceptions import ConfigurationError

if TYPE_CHECKING:
    from app.agents.base import BaseAgent


class AgentRegistry:
    """Registro de agentes por nome e papel. Não é thread-safe (uso single-loop)."""

    def __init__(self) -> None:
        self._by_name: dict[str, BaseAgent] = {}
        self._started: bool = False

    def register(self, agent: BaseAgent) -> None:
        if agent.name in self._by_name:
            raise ConfigurationError(f"Agente '{agent.name}' já registrado")
        self._by_name[agent.name] = agent

    def unregister(self, name: str) -> None:
        self._by_name.pop(name, None)

    def get(self, name: str) -> BaseAgent:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise ConfigurationError(f"Agente '{name}' não encontrado") from exc

    def by_role(self, role: AgentRole) -> list[BaseAgent]:
        return [a for a in self._by_name.values() if a.role == role]

    def require_one(self, role: AgentRole) -> BaseAgent:
        matches = self.by_role(role)
        if not matches:
            raise ConfigurationError(f"Nenhum agente com role {role}")
        if len(matches) > 1:
            raise ConfigurationError(f"Múltiplos agentes com role {role}: {matches}")
        return matches[0]

    def all(self) -> list[BaseAgent]:
        return list(self._by_name.values())

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    async def start_all(self) -> None:
        if self._started:
            return
        for agent in self._by_name.values():
            if agent.enabled:
                await agent.start()
        self._started = True

    async def stop_all(self) -> None:
        for agent in list(self._by_name.values()):
            if agent.started:
                await agent.stop()
        self._started = False