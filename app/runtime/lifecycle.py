from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress

from app.orchestration.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]


class ApplicationLifecycle:
    """Gerencia o ciclo de vida do processo.

    - Inicia o Orchestrator
    - Inicia tasks auxiliares (heartbeat, health, ...)
    - Instala handlers de SIGINT/SIGTERM (Unix: via loop; Windows: via signal.signal)
    - Faz graceful shutdown
    """

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        tasks: list[TaskFactory] | None = None,
        install_signal_handlers: bool = True,
    ) -> None:
        self._orchestrator = orchestrator
        self._task_factories = tasks or []
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()
        self._install_signal_handlers = install_signal_handlers
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    async def start(self) -> None:
        if self._started:
            return
        logger.info("lifecycle.starting")
        await self._orchestrator.start()
        for i, factory in enumerate(self._task_factories):
            task = asyncio.create_task(factory(), name=f"lifecycle-task-{i}")
            self._tasks.append(task)
        if self._install_signal_handlers:
            self._install_signals()
        self._started = True
        logger.info("lifecycle.started")

    async def stop(self) -> None:
        if not self._started:
            return
        logger.info("lifecycle.stopping")
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._orchestrator.stop()
        self._started = False
        logger.info("lifecycle.stopped")

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._shutdown.wait()
        except asyncio.CancelledError:
            logger.info("lifecycle.run_cancelled")
        finally:
            await self.stop()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    # ------------------------------------------------------------------ signals
    def _install_signals(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        # Preferência: loop.add_signal_handler (Unix)
        installed_via_loop = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, self._on_signal, sig)
                installed_via_loop = True

        if installed_via_loop:
            return

        # Fallback Windows (ProactorEventLoop não suporta add_signal_handler)
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, self._on_signal)
            logger.debug("lifecycle.signal_handlers_installed_via_signal")
        except (ValueError, OSError):
            logger.warning("lifecycle.cannot_install_signals")

    def _on_signal(self, sig, _frame=None) -> None:
        name = getattr(sig, "name", str(sig))
        logger.info("lifecycle.signal", extra={"signal": name})
        self.request_shutdown()