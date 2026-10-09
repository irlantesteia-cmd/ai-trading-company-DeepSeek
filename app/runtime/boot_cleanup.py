"""Limpeza de boot: posições FUTURES abertas e ordens condicionais órfãs.

`close_all_futures_positions_on_boot`: fecha todas as posições abertas.

Feature de conveniência para dev/test: evita que runs consecutivas fiquem
bloqueadas pelo `RiskEngine` (regra "posição já aberta em <símbolo>").

**Default False** — habilitar apenas em dev/test via
`CLOSE_POSITIONS_ON_BOOT=true` no `.env`. Em produção, deixar False.

`cancel_orphan_conditional_orders_on_boot`: cancela SL/TP de símbolos sem
posição. Roda sempre (controlado por `CANCEL_PROTECTIVE_ORDERS_ON_CLOSE`).
"""

from __future__ import annotations

import logging

from app.core.config import settings
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

async def cancel_orphan_conditional_orders_on_boot(exchange: Exchange) -> int:
    """Cancela ordens condicionais (SL/TP) de símbolos **sem posição aberta**.

    Órfãs surgem quando o bot está fora do ar e um SL/TP dispara: o irmão
    fica aberto sem posição para proteger e, sendo `reduceOnly`, pode agir
    contra uma posição nova no mesmo símbolo. Ordens de símbolos com posição
    são preservadas.

    Controlado por `CANCEL_PROTECTIVE_ORDERS_ON_CLOSE` (default True), e
    independente de `CLOSE_POSITIONS_ON_BOOT`. Nunca propaga exceções: o
    boot não pode ser abortado por limpeza. Se as posições não puderem ser
    listadas, nada é cancelado (não dá para saber o que é órfã).
    """
    if not settings.cancel_protective_orders_on_close:
        logger.info("boot_cleanup.orphans_disabled")
        return 0

    try:
        positions = await exchange.account.get_futures_positions()
    except Exception:
        logger.exception("boot_cleanup.orphans_positions_failed")
        return 0

    try:
        open_conditional = await exchange.orders.list_open_conditional_orders(None)
    except NotImplementedError:
        return 0
    except Exception:
        logger.exception("boot_cleanup.orphans_list_failed")
        return 0

    with_position = {p.symbol for p in positions if p.quantity != 0}
    orphan_symbols = sorted({o.symbol for o in open_conditional} - with_position)
    if not orphan_symbols:
        logger.info(
            "boot_cleanup.no_orphans",
            extra={"open_conditional": len(open_conditional)},
        )
        return 0

    canceled = 0
    for symbol in orphan_symbols:
        try:
            n = await exchange.orders.cancel_all_algo_orders(symbol)
            canceled += n
            logger.info(
                "boot_cleanup.orphans_canceled",
                extra={"symbol": symbol, "count": n},
            )
        except Exception:
            logger.exception(
                "boot_cleanup.orphans_cancel_failed", extra={"symbol": symbol}
            )
    return canceled
