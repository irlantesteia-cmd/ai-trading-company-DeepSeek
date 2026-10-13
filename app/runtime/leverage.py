"""Alavancagem explícita (Fase 5b): no boot, aplica `RISK_MAX_LEVERAGE` em cada
ativo operado. Sem isso a conta fica com o padrão da Binance (até 20×), e a
checagem de margem do risco calcularia com um valor diferente do real.

Se não der para aplicar num ativo, ele recebe a trava `leverage` (só o ativo,
liberação manual): operar com alavancagem desconhecida não é seguro.
"""

from __future__ import annotations

import logging

from app.exchanges.base.exchange import Exchange
from app.runtime.halt_guard import RULE_LEVERAGE, HaltGuard

logger = logging.getLogger(__name__)


async def apply_leverage_on_boot(
    exchange: Exchange,
    symbols: list[str],
    leverage: int,
    *,
    guard: HaltGuard | None = None,
) -> dict[str, bool]:
    """Retorna, por ativo, se a alavancagem foi aplicada."""
    applied: dict[str, bool] = {}
    for symbol in symbols:
        try:
            await exchange.positions.set_leverage(symbol, leverage)
        except Exception as exc:
            applied[symbol] = False
            logger.exception("leverage.apply_failed", extra={"symbol": symbol, "leverage": leverage})
            if guard is not None:
                await guard.trip(
                    rule=RULE_LEVERAGE,
                    reason=f"não foi possível aplicar alavancagem {leverage}×: {exc!r}",
                    symbol=symbol,
                )
            continue
        applied[symbol] = True
        logger.info("leverage.applied", extra={"symbol": symbol, "leverage": leverage})
    return applied
