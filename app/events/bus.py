import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.exceptions import EventBusError
from app.events.event import Event

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=Event)
Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """
    Event bus assíncrono, in-process, com despacho por tipo exato.
    Handlers são executados sequencialmente por evento; falhas são isoladas.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(
        self, event_type: type[E], handler: Callable[[E], Awaitable[None]]
    ) -> None:
        if not issubclass(event_type, Event):
            raise EventBusError(f"{event_type} não é um Event")
        self._handlers[event_type].append(handler)  # type: ignore[arg-type]

    def unsubscribe(
        self, event_type: type[E], handler: Callable[[E], Awaitable[None]]
    ) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)  # type: ignore[arg-type]

    async def publish(self, event: Event) -> None:
        handlers = list(self._handlers.get(type(event), ()))
        if not handlers:
            logger.debug("event.no_handlers", extra={"event": event.name})
            return
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "event.handler_failed",
                    extra={
                        "event": event.name,
                        "handler": getattr(handler, "__qualname__", str(handler)),
                    },
                )

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)

    def clear(self) -> None:
        self._handlers.clear()


# Instância global — será injetada nos agentes/serviços.
event_bus = EventBus()