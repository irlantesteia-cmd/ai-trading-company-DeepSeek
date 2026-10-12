"""Reconciliação periódica com travas operacionais (R3).

A cada rodada:
- reconcilia ordens (DB × exchange); divergência não resolvida em
  `HALT_RECONCILIATION_RUNS` rodadas seguidas → trava `reconciliation`;
- rodada que nem consegue rodar, `HALT_RECONCILIATION_ERRORS` vezes seguidas
  → trava `reconciliation_error`; chave rejeitada → trava `api_auth` na hora;
- com `STOP_LOSS_ENABLED`, confere que cada posição aberta tem stop-loss
  ativo na exchange; o mesmo símbolo sem stop em `HALT_UNPROTECTED_RUNS`
  rodadas seguidas → trava `unprotected_position`.

Só cria travas e alertas: nunca fecha posição nem envia ordem.
"""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.core.enums import MarketType, OrderType
from app.core.exceptions import ExchangeAuthError
from app.events.bus import EventBus
from app.events.event import HealthCheckFailed
from app.exchanges.base.exchange import Exchange
from app.runtime.halt_guard import (
    RULE_API_AUTH,
    RULE_RECONCILIATION,
    RULE_RECONCILIATION_ERROR,
    RULE_UNPROTECTED_POSITION,
    ConsecutiveFailures,
    HaltGuard,
)
from app.runtime.recovery import Reconciler, ReconciliationReport

logger = logging.getLogger(__name__)

_STOP_TYPES = (OrderType.STOP_MARKET, OrderType.STOP_LIMIT)


class ReconcileMonitor:
    def __init__(
        self,
        *,
        reconciler: Reconciler,
        exchange: Exchange,
        guard: HaltGuard,
        event_bus: EventBus,
        settings: Settings,
        market_type: MarketType = MarketType.FUTURES,
    ) -> None:
        self._reconciler = reconciler
        self._exchange = exchange
        self._guard = guard
        self._event_bus = event_bus
        self._settings = settings
        self._market_type = market_type
        self._unbalanced = ConsecutiveFailures(settings.halt_reconciliation_runs)
        self._errors = ConsecutiveFailures(settings.halt_reconciliation_errors)
        self._unprotected: dict[str, int] = {}

    async def run_once(self) -> ReconciliationReport | None:
        try:
            report = await self._reconciler.reconcile(market_type=self._market_type)
            unprotected = await self._unprotected_symbols()
        except ExchangeAuthError as exc:
            logger.warning("reconcile.auth_failed", extra={"detail": str(exc)})
            await self._guard.trip(
                rule=RULE_API_AUTH, reason=f"corretora rejeitou a chave na reconciliação: {exc}"
            )
            return None
        except Exception as exc:
            logger.exception("reconcile.failed")
            if self._errors.failure():
                await self._guard.trip(
                    rule=RULE_RECONCILIATION_ERROR,
                    reason=f"{self._errors.count} reconciliações seguidas falharam; última: {exc!r}",
                )
            return None

        self._errors.success()
        await self._check_balance(report)
        await self._check_protection(unprotected)
        return report

    async def _check_balance(self, report: ReconciliationReport) -> None:
        if report.balanced:
            self._unbalanced.success()
            return
        await self._event_bus.publish(
            HealthCheckFailed(component="reconciliation", detail=f"unresolved={report.unresolved}")
        )
        if self._unbalanced.failure():
            await self._guard.trip(
                rule=RULE_RECONCILIATION,
                reason=(
                    f"{self._unbalanced.count} reconciliações seguidas com divergência: "
                    f"{report.unresolved}"
                ),
            )

    async def _unprotected_symbols(self) -> set[str] | None:
        """Símbolos com posição aberta e sem stop-loss ativo; None se a
        verificação está desligada."""
        if (
            not self._settings.stop_loss_enabled
            or self._settings.halt_unprotected_runs <= 0
            or self._market_type is not MarketType.FUTURES
        ):
            return None
        positions = await self._exchange.account.get_futures_positions()
        open_symbols = {p.symbol for p in positions if p.is_open}
        if not open_symbols:
            return set()
        # strict: uma falha na consulta não pode parecer "sem stop".
        orders = await self._exchange.orders.list_open_conditional_orders(None, strict=True)
        protected = {o.symbol for o in orders if o.type in _STOP_TYPES and o.is_open}
        return open_symbols - protected

    async def _check_protection(self, unprotected: set[str] | None) -> None:
        if unprotected is None:
            return
        self._unprotected = {s: self._unprotected.get(s, 0) + 1 for s in unprotected}
        if unprotected:
            logger.warning(
                "reconcile.unprotected_positions", extra={"symbols": sorted(unprotected)}
            )
        late = sorted(
            s for s, n in self._unprotected.items() if n >= self._settings.halt_unprotected_runs
        )
        if late:
            await self._guard.trip(
                rule=RULE_UNPROTECTED_POSITION,
                reason=(
                    f"posição aberta sem stop-loss em {self._settings.halt_unprotected_runs} "
                    f"verificações seguidas: {', '.join(late)}"
                ),
            )
