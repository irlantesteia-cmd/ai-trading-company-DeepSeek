"""Treino automático dos modelos ML do MLAgent.

Roda no boot, depois que o backfill de candles popula o DB. Isola falhas
por símbolo: um dataset ruim não impede os demais de treinarem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.enums import MarketType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoTrainReport:
    symbol: str
    success: bool
    version: str | None = None
    n_train: int = 0
    n_test: int = 0
    test_accuracy: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class AutoTrainSummary:
    reports: list[AutoTrainReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.reports)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.reports if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.reports if not r.success)


async def autotrain_all(
    *,
    ml_agent,
    symbols: list[str],
    market_type: MarketType,
    interval: str,
    limit: int = 500,
) -> AutoTrainSummary:
    """Treina um modelo por símbolo, isolando falhas.

    Espera receber um `MLAgent` (duck-typed) com `async train(symbol=..., ...)`
    que devolva um `TrainingResult` com `.metadata.version`,
    `.metadata.n_train`, `.metadata.n_test` e `.test_accuracy`.
    """
    reports: list[AutoTrainReport] = []

    for symbol in symbols:
        try:
            result = await ml_agent.train(
                symbol=symbol,
                market_type=market_type,
                interval=interval,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 — isola falha por símbolo intencionalmente
            # Qualquer erro do treino (sklearn, numpy, RuntimeError, ValueError
            # de dados insuficientes, etc.) precisa virar um report de falha
            # por símbolo. Estreitar a exceção deixaria tipos não previstos
            # derrubarem o loop inteiro — o oposto do objetivo desta função.
            detail = str(exc) or type(exc).__name__
            reports.append(
                AutoTrainReport(symbol=symbol, success=False, error=detail)
            )
            logger.warning(
                "ml.autotrain_symbol_failed",
                extra={"symbol": symbol, "error": detail},
            )
            continue

        report = AutoTrainReport(
            symbol=symbol,
            success=True,
            version=result.metadata.version,
            n_train=result.metadata.n_train,
            n_test=result.metadata.n_test,
            test_accuracy=result.test_accuracy,
        )
        reports.append(report)
        logger.info(
            "ml.autotrain_symbol_done",
            extra={
                "symbol": symbol,
                "version": report.version,
                "n_train": report.n_train,
                "n_test": report.n_test,
                "test_accuracy": report.test_accuracy,
            },
        )

    summary = AutoTrainSummary(reports=reports)
    logger.info(
        "ml.autotrain_completed",
        extra={
            "total": summary.total,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
        },
    )
    return summary