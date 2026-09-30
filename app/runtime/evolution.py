from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.github.types import ProposedChange, PullRequestResult
from app.github.workflows import GitHubWorkflows

logger = logging.getLogger(__name__)

MetricsProvider = Callable[[], Awaitable[dict[str, float]]]
ChangeGenerator = Callable[[dict[str, float]], Awaitable[ProposedChange | None]]


class EvolutionLoop:
    """Loop periódico: coleta métricas → decide → propõe PR.

    `change_generator` retorna `None` quando não há mudança a propor.
    Cooldown impede propor PRs em rajada.
    """

    def __init__(
        self,
        *,
        workflows: GitHubWorkflows,
        metrics_provider: MetricsProvider,
        change_generator: ChangeGenerator,
        interval_seconds: float = 3600.0,
        cooldown_seconds: float = 86400.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds deve ser > 0")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds deve ser >= 0")
        self._workflows = workflows
        self._metrics_provider = metrics_provider
        self._change_generator = change_generator
        self._interval = interval_seconds
        self._cooldown = cooldown_seconds
        self._clock = clock or time.monotonic
        self._last_proposal_at: float | None = None
        self._cycles = 0
        self._proposals = 0

    @property
    def cycles(self) -> int:
        return self._cycles

    @property
    def proposals(self) -> int:
        return self._proposals

    def _cooldown_active(self) -> bool:
        if self._last_proposal_at is None:
            return False
        return (self._clock() - self._last_proposal_at) < self._cooldown

    async def tick(self) -> PullRequestResult | None:
        """Um ciclo único, sem sleep. Retorna o PR se houve proposta."""
        self._cycles += 1
        if not self._workflows.enabled:
            return None
        if self._cooldown_active():
            logger.debug("evolution.cooldown_active")
            return None

        metrics = await self._metrics_provider()
        change = await self._change_generator(metrics)
        if change is None:
            return None

        result = await self._workflows.propose_change(change)
        self._last_proposal_at = self._clock()
        self._proposals += 1
        return result

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("evolution.tick_failed")
            await asyncio.sleep(self._interval)