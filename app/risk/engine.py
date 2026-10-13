from __future__ import annotations

import logging
from decimal import Decimal

from app.core.enums import RiskAction
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.risk.limits import RiskLimits

logger = logging.getLogger(__name__)

RULE_HALTED = "halted"
RULE_CONFIDENCE = "confidence"
RULE_DAILY_LOSS = "daily_loss"
RULE_CONSECUTIVE_LOSSES = "consecutive_losses"
RULE_TOTAL_EXPOSURE = "total_exposure"
RULE_MARGIN = "margin"
# Rejeições que viram trava global persistente (liberação só manual).
AUTO_HALT_RULES = frozenset({RULE_DAILY_LOSS, RULE_CONSECUTIVE_LOSSES})


class RiskEngine:
    """Avalia sinais contra limites configuráveis.

    Regras (avaliadas em ordem, primeira falha rejeita):
        0. nenhuma trava de trading ativa (global ou do símbolo)
        1. confidence >= min_confidence
        2. daily_pnl > -(menor entre max_daily_loss e max_daily_loss_pct do equity)
        3. consecutive_losses < max_consecutive_losses
        4. exposição total + uma posição cheia <= max_total_notional (absoluto)
           e <= max_total_exposure_x × equity (relativo ao capital)
        5. open_positions_count < max_open_positions
        6. nenhuma direção com `max_same_direction` posições abertas
        7. não há posição já aberta no mesmo símbolo
        8. nenhum símbolo correlacionado já tem posição
        9. saldo livre cobre a margem de uma posição cheia (notional /
           alavancagem × folga)
    """

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    async def evaluate(self, signal: Signal, state: PortfolioState) -> RiskDecision:
        limits = self._limits

        halt = state.halt_for(signal.symbol)
        if halt is not None:
            where = "global" if halt.scope == "GLOBAL" else halt.symbol
            return self._reject(
                signal, f"TRADING HALTED ({where}): {halt.reason}", RULE_HALTED
            )

        if signal.confidence < limits.min_confidence:
            return self._reject(
                signal,
                f"confidence {signal.confidence:.3f} < {limits.min_confidence:.3f}",
                RULE_CONFIDENCE,
            )

        daily_limit = self.daily_loss_limit(state.equity)
        if state.daily_pnl <= -daily_limit:
            return self._reject(
                signal,
                f"daily loss limit atingido: pnl={state.daily_pnl:.2f} "
                f"<= -{daily_limit:.2f}",
                RULE_DAILY_LOSS,
            )

        if (
            limits.max_consecutive_losses > 0
            and state.consecutive_losses >= limits.max_consecutive_losses
        ):
            return self._reject(
                signal,
                f"max_consecutive_losses={limits.max_consecutive_losses} atingido "
                f"(atual={state.consecutive_losses})",
                RULE_CONSECUTIVE_LOSSES,
            )

        projected = state.total_notional + limits.max_position_notional_per_symbol
        if projected > limits.max_total_notional:
            return self._reject(
                signal,
                f"max_total_notional={limits.max_total_notional} excedido: "
                f"atual={state.total_notional:.2f} + nova posição até "
                f"{limits.max_position_notional_per_symbol}",
                "total_notional",
            )

        if limits.max_total_exposure_x > 0 and state.equity > 0:
            exposure_cap = state.equity * limits.max_total_exposure_x
            if projected > exposure_cap:
                return self._reject(
                    signal,
                    f"exposição total excederia {limits.max_total_exposure_x}× o equity: "
                    f"atual={state.total_notional:.2f} + nova posição até "
                    f"{limits.max_position_notional_per_symbol} > {exposure_cap:.2f}",
                    RULE_TOTAL_EXPOSURE,
                )

        if state.open_positions_count >= limits.max_open_positions:
            return self._reject(
                signal,
                f"max_open_positions={limits.max_open_positions} atingido",
                "max_open_positions",
            )

        current_in_direction = state.count_in_direction(signal.direction)
        if current_in_direction >= limits.max_same_direction:
            return self._reject(
                signal,
                f"max_same_direction={limits.max_same_direction} atingido "
                f"para {signal.direction} (atual={current_in_direction})",
                "same_direction",
            )

        if state.has_position(signal.symbol):
            return self._reject(
                signal,
                f"posição já aberta em {signal.symbol}",
                "symbol_open",
            )

        for group_name, symbols in limits.correlated_groups.items():
            if signal.symbol not in symbols:
                continue
            conflicting = state.symbols().intersection(symbols)
            if conflicting:
                return self._reject(
                    signal,
                    f"correlação (grupo '{group_name}'): já há posição em "
                    f"{sorted(conflicting)}",
                    "correlation",
                )

        if limits.margin_buffer > 0 and state.available_balance is not None:
            leverage = Decimal(max(1, limits.max_leverage))
            required = limits.max_position_notional_per_symbol / leverage * limits.margin_buffer
            if state.available_balance < required:
                return self._reject(
                    signal,
                    f"margem insuficiente: livre={state.available_balance:.2f} < "
                    f"{required:.2f} (posição até {limits.max_position_notional_per_symbol} "
                    f"/ {leverage}× × {limits.margin_buffer})",
                    RULE_MARGIN,
                )

        return RiskDecision(
            signal_id=signal.signal_id,
            action=RiskAction.APPROVE,
            reason="aprovado",
            max_leverage=limits.max_leverage,
        )

    def daily_loss_limit(self, equity: Decimal) -> Decimal:
        """Perda diária máxima efetiva: o menor entre o absoluto e o % do equity."""
        limit = self._limits.max_daily_loss
        if self._limits.max_daily_loss_pct > 0 and equity > 0:
            limit = min(limit, equity * self._limits.max_daily_loss_pct / 100)
        return limit

    @staticmethod
    def _reject(signal: Signal, reason: str, rule: str) -> RiskDecision:
        logger.info(
            "risk.rejected",
            extra={"signal_id": signal.signal_id, "reason": reason},
        )
        return RiskDecision(
            signal_id=signal.signal_id,
            action=RiskAction.REJECT,
            reason=reason,
            rule=rule,
        )