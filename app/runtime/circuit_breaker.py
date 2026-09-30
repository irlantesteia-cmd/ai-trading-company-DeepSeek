from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar

from app.core.exceptions import CircuitBreakerOpenError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Disjuntor assíncrono.

    - CLOSED: chamadas passam. Falhas consecutivas >= failure_threshold → OPEN.
    - OPEN: chamadas são rejeitadas com CircuitBreakerOpenError. Após
      recovery_timeout, transiciona para HALF_OPEN.
    - HALF_OPEN: uma chamada teste é permitida. Sucessos acumulados
      (>= success_threshold) → CLOSED. Qualquer falha → OPEN imediatamente.
    """

    def __init__(
        self,
        *,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold deve ser > 0")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout deve ser > 0")
        if success_threshold <= 0:
            raise ValueError("success_threshold deve ser > 0")

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._clock = clock or time.monotonic

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def successes(self) -> int:
        return self._successes

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"circuit '{self.name}' está aberto"
                )

        try:
            result = await fn()
        except Exception:
            async with self._lock:
                self._on_failure()
            raise
        else:
            async with self._lock:
                self._on_success()
            return result

    # ------------------------------------------------------------------ internals
    def _maybe_transition_to_half_open(self) -> None:
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return
        if self._clock() - self._opened_at >= self.recovery_timeout:
            logger.info("circuit.half_open", extra={"circuit": self.name})
            self._state = CircuitState.HALF_OPEN
            self._successes = 0
            self._failures = 0

    def _on_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self.success_threshold:
                self._close()
            return
        self._failures = 0

    def _on_failure(self) -> None:
        self._failures += 1
        if self._state == CircuitState.HALF_OPEN:
            self._open()
            return
        if self._failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._successes = 0
        logger.warning(
            "circuit.opened",
            extra={"circuit": self.name, "failures": self._failures},
        )

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = None
        logger.info("circuit.closed", extra={"circuit": self.name})