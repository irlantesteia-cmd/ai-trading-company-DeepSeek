from __future__ import annotations

import logging

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole
from app.events.event import Event, HealthCheckFailed, OrderFilled, OrderSubmitted

logger = logging.getLogger(__name__)


class QAAgent(BaseAgent):
    """Verifica invariantes em execução.

    Invariantes default:
    - OrderFilled: filled_quantity > 0, average_price > 0
    - OrderSubmitted: client_order_id não vazio

    Violações publicam HealthCheckFailed (component="qa").
    """

    role = AgentRole.QA
    name = "qa"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._violations = 0

    @property
    def violations(self) -> int:
        return self._violations

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {
            OrderFilled: self._check_fill,
            OrderSubmitted: self._check_submission,
        }

    async def _check_fill(self, event: Event) -> None:
        if not isinstance(event, OrderFilled):
            return
        problems: list[str] = []
        if event.filled_quantity <= 0:
            problems.append("filled_quantity <= 0")
        if event.average_price <= 0:
            problems.append("average_price <= 0")
        if problems:
            await self._report("order_filled", "; ".join(problems))

    async def _check_submission(self, event: Event) -> None:
        if not isinstance(event, OrderSubmitted):
            return
        if not event.client_order_id:
            await self._report("order_submitted", "client_order_id vazio")

    async def _report(self, kind: str, detail: str) -> None:
        self._violations += 1
        logger.error("qa.violation", extra={"kind": kind, "detail": detail})
        await self.context.event_bus.publish(
            HealthCheckFailed(component="qa", detail=f"{kind}: {detail}")
        )