"""Verificação periódica da conexão com o banco.

`ping_database_once()` faz um `SELECT 1` e, em caso de falha, publica
`HealthCheckFailed(component="database")`. O `EngineeringAgent` reage
a esses eventos conforme sua política.

Nunca levanta exceção — erros são convertidos em evento + log.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.database.session import check_connection
from app.events.bus import EventBus
from app.events.event import HealthCheckFailed

logger = logging.getLogger(__name__)

CheckFn = Callable[[], Awaitable[None]]


async def ping_database_once(
    *,
    event_bus: EventBus,
    check_fn: CheckFn = check_connection,
) -> bool:
    """Faz um ping único. Retorna True se OK, False se falhou.

    Nunca levanta — falhas viram `HealthCheckFailed(component="database")`.
    """
    try:
        await check_fn()
    except Exception as exc:  # noqa: BLE001 — absorve qualquer falha de DB
        # Qualquer erro do check (ConnectionRefusedError, TimeoutError,
        # OperationalError do SQLAlchemy, etc.) precisa virar um evento de
        # health. Estreitar a exceção deixaria tipos não previstos derrubarem
        # o loop de ping — o oposto do objetivo desta função.
        detail = str(exc) or type(exc).__name__
        logger.warning("db.ping_failed", extra={"error": detail})
        await event_bus.publish(
            HealthCheckFailed(component="database", detail=detail)
        )
        return False
    return True


async def run_database_ping_loop(
    *,
    event_bus: EventBus,
    interval_seconds: float,
    check_fn: CheckFn = check_connection,
) -> None:
    """Loop infinito: ping a cada `interval_seconds`.

    Erros de um ciclo não interrompem os ciclos seguintes — `ping_database_once`
    já absorve tudo.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds deve ser > 0")
    while True:
        await ping_database_once(event_bus=event_bus, check_fn=check_fn)
        await asyncio.sleep(interval_seconds)