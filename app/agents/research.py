from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.core.enums import AgentRole
from app.github.workflows import GitHubWorkflows

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    """Pesquisa padrões/hipóteses e, opcionalmente, abre issues no GitHub.

    `propose_hypothesis` só funciona com `workflows` injetado. Sem ele,
    apenas loga — mantendo a operação padrão livre de dependências do GitHub.
    """

    role = AgentRole.RESEARCH
    name = "research"

    def __init__(
        self,
        context,
        *,
        workflows: GitHubWorkflows | None = None,
    ) -> None:
        super().__init__(context)
        self._workflows = workflows

    @property
    def workflows(self) -> GitHubWorkflows | None:
        return self._workflows

    async def propose_hypothesis(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> int | None:
        """Cria issue no GitHub. Retorna o número da issue, ou None se desabilitado."""
        if self._workflows is None or not self._workflows.enabled:
            logger.info(
                "research.hypothesis_skipped",
                extra={"title": title, "reason": "github desabilitado"},
            )
            return None
        return await self._workflows.open_issue(
            title=title, body=body, labels=labels or ["research"]
        )