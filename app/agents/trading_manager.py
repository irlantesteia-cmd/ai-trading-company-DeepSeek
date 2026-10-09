from __future__ import annotations

import logging
from decimal import Decimal

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import AgentRole, RiskAction
from app.core.exceptions import ExchangeError
from app.domain.models.order import Order
from app.domain.models.signal import Signal
from app.events.event import Event, SignalApproved, SignalGenerated, SignalRejected
from app.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)


class TradingManager(BaseAgent):
    """Orquestra o pipeline: Signal → Risk → Portfolio → Execution.

    Escuta `SignalGenerated`. O que fazer com cada sinal depende de
    `settings.signal_auto_execution_enabled`:
    - `False`: sinal é logado e ignorado.
    - `True`: `process_signal()` roda — risco → sizing → ordem.
    """

    role = AgentRole.TRADING_MANAGER
    name = "trading_manager"

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
            await self.process_signal(event.signal)
        except (ValueError, ExchangeError) as exc:
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
        positions = await self.context.exchange.account.get_futures_positions()
        balance = await self.context.exchange.account.get_futures_balance("USDT")
        return PortfolioState(
            equity=balance.total,
            daily_pnl=Decimal(0),
            positions=positions,
        )