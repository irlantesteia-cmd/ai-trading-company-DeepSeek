from __future__ import annotations

import logging

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole, RiskAction
from app.core.exceptions import ExchangeAuthError, ExchangeError
from app.domain.models.order import Order
from app.domain.models.signal import Signal
from app.events.event import Event, SignalApproved, SignalGenerated, SignalRejected
from app.portfolio.state import PortfolioState
from app.portfolio.state_loader import load_portfolio_state
from app.runtime.halt_guard import (
    RULE_API_AUTH,
    RULE_API_ERRORS,
    ConsecutiveFailures,
    HaltGuard,
)

logger = logging.getLogger(__name__)


class TradingManager(BaseAgent):
    """Orquestra o pipeline: Signal → Risk → Portfolio → Execution.

    Escuta `SignalGenerated`. O que fazer com cada sinal depende de
    `settings.signal_auto_execution_enabled`:
    - `False`: sinal é logado e ignorado.
    - `True`: `process_signal()` roda — risco → sizing → ordem.

    Erros da corretora ao executar (R3): chave rejeitada trava na hora;
    `HALT_API_ERRORS` falhas seguidas criam trava global. Uma ordem enviada
    com sucesso zera a contagem.
    """

    role = AgentRole.TRADING_MANAGER
    name = "trading_manager"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._api_errors = ConsecutiveFailures(context.settings.halt_api_errors)
        self._guard = HaltGuard(
            session_factory=context.session_factory,
            event_bus=context.event_bus,
            created_by=self.name,
        )

    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {SignalGenerated: self._on_signal_generated}

    async def _on_signal_generated(self, event: Event) -> None:
        if not isinstance(event, SignalGenerated):
            return

        if not self.context.settings.signal_auto_execution_enabled:
            logger.info(
                "trading_manager.signal_ignored",
                extra={
                    "signal_id": event.signal_id,
                    "symbol": event.symbol,
                    "direction": event.direction,
                    "confidence": event.confidence,
                    "reason": "SIGNAL_AUTO_EXECUTION_ENABLED=false",
                },
            )
            return

        if event.signal is None:
            logger.warning(
                "trading_manager.signal_payload_missing",
                extra={"signal_id": event.signal_id, "symbol": event.symbol},
            )
            return

        try:
            order = await self.process_signal(event.signal)
        except (ValueError, ExchangeError) as exc:
            if isinstance(exc, ExchangeError):
                await self._on_exchange_error(exc, event.symbol)
            # Estado operacional (equity zero, dados incompletos, exchange
            # rejeitou) — mundo real, não bug. Log como warning sem traceback.
            logger.warning(
                "trading_manager.signal_processing_skipped",
                extra={
                    "signal_id": event.signal_id,
                    "symbol": event.symbol,
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                },
            )
        except Exception:
            logger.exception(
                "trading_manager.process_signal_failed",
                extra={"signal_id": event.signal_id, "symbol": event.symbol},
            )
        else:
            if order is not None:
                self._api_errors.success()

    async def _on_exchange_error(self, exc: ExchangeError, symbol: str) -> None:
        if isinstance(exc, ExchangeAuthError):
            await self._guard.trip(
                rule=RULE_API_AUTH, reason=f"corretora rejeitou a chave ({symbol}): {exc}"
            )
            return
        if self._api_errors.failure():
            await self._guard.trip(
                rule=RULE_API_ERRORS,
                reason=(
                    f"{self._api_errors.count} erros seguidos da corretora ao executar "
                    f"sinais; último ({symbol}): {exc}"
                ),
            )

    async def process_signal(self, signal: Signal) -> Order | None:
        registry = self.context.registry

        risk = registry.require_one(AgentRole.RISK)
        portfolio = registry.require_one(AgentRole.PORTFOLIO)
        execution = registry.require_one(AgentRole.EXECUTION)

        state = await self._load_state()
        decision = await risk.evaluate(signal, state)  # type: ignore[attr-defined]

        if decision.action == RiskAction.REJECT:
            await self.context.event_bus.publish(
                SignalRejected(signal_id=signal.signal_id, reason=decision.reason)
            )
            logger.info(
                "trading.signal_rejected",
                extra={"signal_id": signal.signal_id, "reason": decision.reason},
            )
            return None

        intent = await portfolio.size(signal, decision, state)  # type: ignore[attr-defined]

        await self.context.event_bus.publish(
            SignalApproved(
                signal_id=signal.signal_id,
                approved_quantity=float(intent.quantity),
            )
        )

        order = await execution.execute(intent)  # type: ignore[attr-defined]

        logger.info(
            "trading.order_placed",
            extra={
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "order_id": order.exchange_order_id,
                "quantity": str(intent.quantity),
            },
        )
        return order

    async def _load_state(self) -> PortfolioState:
        return await load_portfolio_state(
            self.context.exchange, self.context.session_factory
        )
