"""Fecha todas as posições FUTURES abertas no boot.

Feature de conveniência para dev/test: evita que runs consecutivas fiquem
bloqueadas pelo `RiskEngine` (regra "posição já aberta em <símbolo>").

**Default False** — habilitar apenas em dev/test via
`CLOSE_POSITIONS_ON_BOOT=true` no `.env`. Em produção, deixar False.
"""

from __future__ import annotations

import logging

from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)


async def close_all_futures_positions_on_boot(exchange: Exchange) -> int:
    """Fecha todas as posições FUTURES abertas na exchange.

    Retorna o número de posições fechadas com sucesso. Falhas de rede ou
    de fechamento individual são logadas mas nunca propagadas — o boot
    não pode ser abortado por causa de cleanup.

    Isolamento por símbolo: uma falha em SOLUSDT não impede XRPUSDT de
    ser fechado.
    """
    try:
        positions = await exchange.account.get_futures_positions()
    except Exception:
        logger.exception("boot_cleanup.list_failed")
        return 0

    if not positions:
        logger.info("boot_cleanup.no_positions")
        return 0

    logger.info("boot_cleanup.closing", extra={"count": len(positions)})

    closed = 0
    for pos in positions:
        try:
            await exchange.positions.close_position(pos.symbol, pos.position_side)
            logger.info(
                "boot_cleanup.closed",
                extra={
                    "symbol": pos.symbol,
                    "position_side": pos.position_side.value,
                    "quantity": str(pos.quantity),
                },
            )
            closed += 1
        except Exception:
            logger.exception(
                "boot_cleanup.close_failed",
                extra={
                    "symbol": pos.symbol,
                    "position_side": pos.position_side.value,
                },
            )

    logger.info(
        "boot_cleanup.done",
        extra={"closed": closed, "total": len(positions)},
    )
    return closed