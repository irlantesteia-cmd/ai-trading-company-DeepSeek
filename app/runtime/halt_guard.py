"""Porta única para travas automáticas de trading (R2 + R3).

Quem detecta um problema crítico (risco, execução, reconciliação) chama
`HaltGuard.trip`: a trava é gravada em `trading_halts` sem duplicar (mesmo
escopo, símbolo e regra) e um `HealthCheckFailed("trading_halt")` é publicado
uma única vez. Travas automáticas só são liberadas por um humano
(`scripts/trading_control.py resume`) e bloqueiam apenas **novas entradas**:
posições abertas seguem com SL, TP e saída por tempo.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.enums import HaltScope, HaltSource
from app.database.repositories.trading_halt import TradingHaltRepository
from app.events.bus import EventBus
from app.events.event import HealthCheckFailed

logger = logging.getLogger(__name__)

# Regras operacionais (R3). As de perda (R2) vêm do RiskEngine.
RULE_RECONCILIATION = "reconciliation"
RULE_RECONCILIATION_ERROR = "reconciliation_error"
RULE_API_AUTH = "api_auth"
RULE_API_ERRORS = "api_errors"
RULE_PROTECTION = "protection"
RULE_PROTECTION_FAILURE = "protection_failure"
RULE_UNPROTECTED_POSITION = "unprotected_position"
RULE_LEVERAGE = "leverage"


class HaltGuard:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker | None,
        event_bus: EventBus,
        created_by: str,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._created_by = created_by

    async def trip(self, *, rule: str, reason: str, symbol: str | None = None) -> bool:
        """Cria a trava (global, ou só do `symbol`). True se criou agora;
        False se já existia ou se não deu para gravar (falha só é logada)."""
        if self._session_factory is None:
            logger.warning("halt_guard.no_database", extra={"rule": rule, "reason": reason})
            return False
        try:
            async with self._session_factory() as session:
                created = await TradingHaltRepository(session).create_if_absent(
                    scope=HaltScope.SYMBOL if symbol else HaltScope.GLOBAL,
                    symbol=symbol,
                    source=HaltSource.AUTO,
                    rule=rule,
                    reason=reason,
                    created_by=self._created_by,
                )
                await session.commit()
        except Exception:
            logger.exception("halt_guard.trip_failed", extra={"rule": rule, "symbol": symbol})
            return False
        if created is None:
            return False  # já havia trava ativa por esta regra
        target = f" {symbol}" if symbol else ""
        logger.warning(
            "trading.halted",
            extra={"rule": rule, "symbol": symbol, "reason": reason, "by": self._created_by},
        )
        await self._event_bus.publish(
            HealthCheckFailed(
                component="trading_halt",
                detail=f"TRADING HALTED ({rule}{target}): {reason}",
            )
        )
        return True


class ConsecutiveFailures:
    """Conta falhas seguidas; um sucesso zera. `threshold` 0 desliga."""

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self.count = 0

    def failure(self) -> bool:
        """Registra a falha; True quando a contagem atinge o limite."""
        self.count += 1
        return self.threshold > 0 and self.count >= self.threshold

    def success(self) -> None:
        self.count = 0
