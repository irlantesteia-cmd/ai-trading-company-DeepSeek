from __future__ import annotations

import logging

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole
from app.events.event import Event, HealthCheckFailed, RiskLimitBreached
from app.github.types import FileChange, ProposedChange
from app.github.workflows import GitHubWorkflows
from app.runtime.heartbeat import HeartbeatMonitor

logger = logging.getLogger(__name__)


class EngineeringAgent(BaseAgent):
    """Monitora saúde e propõe correções via PR controlado.

    Reações:
    - `HealthCheckFailed` → incrementa contador.
    - `RiskLimitBreached` → log.
    - `propose_fix(...)` → abre PR via `GitHubWorkflows` (se injetado e habilitado).
    """

    role = AgentRole.ENGINEERING
    name = "engineering"

    def __init__(
        self,
        context,
        *,
        heartbeat: HeartbeatMonitor | None = None,
        workflows: GitHubWorkflows | None = None,
        failures_before_fix: int = 3,
    ) -> None:
        super().__init__(context)
        self._heartbeat = heartbeat or HeartbeatMonitor()
        self._workflows = workflows
        self._failures_before_fix = failures_before_fix
        self._failures_seen = 0

    @property
    def heartbeat(self) -> HeartbeatMonitor:
        return self._heartbeat

    @property
    def failures_seen(self) -> int:
        return self._failures_seen

    @property
    def workflows(self) -> GitHubWorkflows | None:
        return self._workflows

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {
            HealthCheckFailed: self._on_health_failure,
            RiskLimitBreached: self._on_risk_breach,
        }

    async def _on_health_failure(self, event: Event) -> None:
        if not isinstance(event, HealthCheckFailed):
            return
        self._failures_seen += 1
        logger.error(
            "engineering.health_failure",
            extra={"component": event.component, "detail": event.detail},
        )

    async def _on_risk_breach(self, event: Event) -> None:
        if not isinstance(event, RiskLimitBreached):
            return
        logger.warning(
            "engineering.risk_breach",
            extra={"rule": event.rule, "detail": event.detail},
        )

    async def propose_fix(
        self,
        *,
        title: str,
        body: str,
        files: list[FileChange],
        branch_name: str,
    ) -> int | None:
        """Abre PR de correção. Retorna o número do PR, ou None se desabilitado."""
        if self._workflows is None or not self._workflows.enabled:
            logger.info(
                "engineering.fix_skipped",
                extra={"title": title, "reason": "github desabilitado"},
            )
            return None
        result = await self._workflows.propose_change(
            ProposedChange(
                title=title,
                body=body,
                branch_name=branch_name,
                files=files,
                labels=["auto-remediation"],
                draft=True,
            )
        )
        return result.number

    async def on_stop(self) -> None:
        self._heartbeat.reset()