r"""Modo sombra (Fase 6): teste para a frente da estratégia L6, sem ordens.

Roda separado do bot e só lê dados públicos da Binance (sem chave). A cada
vela de 4h refaz a simulação desde o T0 registrado em
`docs/research/hypotheses.json` e grava os trades hipotéticos em
`shadow_trades` (Grafana: linha "Modo sombra"). Sem a hipótese registrada,
só avisa e espera.

Uso (PowerShell):
    docker compose up -d --build shadow             # em container (recomendado)
    .\.venv\Scripts\python scripts/run_shadow.py     # local
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.backtest.shadow_runner import DbShadowStore, ShadowRunner
from app.core.config import settings
from app.core.logging import setup_logging
from app.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


def seconds_until_next_cycle(cycle_s: float, offset_s: float, now: float | None = None) -> float:
    """Até o próximo múltiplo de `cycle_s` (época UTC) mais `offset_s`: com
    4h, alguns segundos depois do fechamento de cada vela de 4h (e de 1d)."""
    now = time.time() if now is None else now
    return cycle_s - ((now - offset_s) % cycle_s)


async def main() -> None:
    setup_logging(settings.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    runner = ShadowRunner(
        hypothesis=settings.shadow_hypothesis,
        registry_path=Path(settings.shadow_registry_path),
        store=DbShadowStore(AsyncSessionLocal),
    )
    logger.info("shadow.starting", extra={"hypothesis": settings.shadow_hypothesis})
    while True:
        try:
            await runner.run_once()
        except Exception:
            logger.exception("shadow.cycle_failed")
        await asyncio.sleep(seconds_until_next_cycle(settings.shadow_cycle_s, settings.shadow_offset_s))


if __name__ == "__main__":
    asyncio.run(main())
