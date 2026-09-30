from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

logger = logging.getLogger(__name__)

ComponentStatus = Literal["ok", "degraded", "down"]
CheckFn = Callable[[], Awaitable[object]]


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: ComponentStatus
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class HealthReport:
    components: list[ComponentHealth]

    @property
    def status(self) -> ComponentStatus:
        if any(c.status == "down" for c in self.components):
            return "down"
        if any(c.status == "degraded" for c in self.components):
            return "degraded"
        return "ok"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "components": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": c.detail,
                    "checked_at": c.checked_at.isoformat(),
                }
                for c in self.components
            ],
        }


class HealthChecker:
    """Coleta status de componentes plugáveis (exchange, DB, redis, custom).

    Um componente que lança exceção é classificado como "down" — nunca derruba
    o health check inteiro.
    """

    def __init__(self) -> None:
        self._checks: list[tuple[str, CheckFn]] = []

    def register(self, name: str, check: CheckFn) -> None:
        self._checks.append((name, check))

    def names(self) -> list[str]:
        return [name for name, _ in self._checks]

    async def run(self) -> HealthReport:
        results: list[ComponentHealth] = []
        for name, check in self._checks:
            try:
                detail = await check()
                results.append(
                    ComponentHealth(name=name, status="ok", detail=str(detail or ""))
                )
            except Exception as exc:  # noqa: BLE001 — isola um componente dos demais
                logger.warning(
                    "health.check_failed",
                    extra={"component": name, "error": str(exc)},
                )
                results.append(
                    ComponentHealth(name=name, status="down", detail=str(exc))
                )
        return HealthReport(components=results)