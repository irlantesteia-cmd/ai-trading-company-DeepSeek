from __future__ import annotations

import logging

from app.core.enums import RiskAction
from app.domain.models.risk import RiskDecision
from app.domain.models.signal import Signal
from app.portfolio.state import PortfolioState
from app.risk.limits import RiskLimits

logger = logging.getLogger(__name__)


class RiskEngine:
    """Avalia sinais contra limites configuráveis.

    Regras (avaliadas em ordem, primeira falha rejeita):
        1. confidence >= min_confidence
        2. daily_pnl > -max_daily_loss
        3. open_positions_count < max_open_positions
        4. nenhuma direção com `max_same_direction` posições abertas
        5. não há posição já aberta no mesmo símbolo
        6. nenhum símbolo correlacionado já tem posição
    """

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    async def evaluate(self, signal: Signal, state: PortfolioState) -> RiskDecision:
        limits = self._limits

        if signal.confidence < limits.min_confidence:
            return self._reject(
                signal,
                f"confidence {signal.confidence:.3f} < {limits.min_confidence:.3f}",
            )

        if state.daily_pnl <= -limits.max_daily_loss:
            return self._reject(
                signal,
                f"daily loss limit atingido: pnl={state.daily_pnl} "
                f"<= -{limits.max_daily_loss}",
            )

        if state.open_positions_count >= limits.max_open_positions:
            return self._reject(
                signal,
                f"max_open_positions={limits.max_open_positions} atingido",
            )

        current_in_direction = state.count_in_direction(signal.direction)
        if current_in_direction >= limits.max_same_direction:
            return self._reject(
                signal,
                f"max_same_direction={limits.max_same_direction} atingido "
                f"para {signal.direction} (atual={current_in_direction})",
            )

        if state.has_position(signal.symbol):
            return self._reject(
                signal,
                f"posição já aberta em {signal.symbol}",
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
                )

        return RiskDecision(
            signal_id=signal.signal_id,
            action=RiskAction.APPROVE,
            reason="aprovado",
            max_leverage=limits.max_leverage,
        )

    @staticmethod
    def _reject(signal: Signal, reason: str) -> RiskDecision:
        logger.info(
            "risk.rejected",
            extra={"signal_id": signal.signal_id, "reason": reason},
        )
        return RiskDecision(
            signal_id=signal.signal_id,
            action=RiskAction.REJECT,
            reason=reason,
        )