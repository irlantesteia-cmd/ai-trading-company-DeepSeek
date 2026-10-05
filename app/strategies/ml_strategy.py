"""Estratégia que delega ao modelo ML treinado do símbolo.

Carregamento **lazy**: o modelo é lido do disco na primeira chamada a
`generate()`. Isso desacopla o registro dos agentes (no boot) da ordem
backfill → autotrain → modelos disponíveis. Se um candle chegar antes de
o modelo ser escrito, `generate()` retorna `None` na primeira chamada e
tenta de novo na próxima.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.domain.models.signal import Signal
from app.features.pipeline import FeaturePipeline, default_pipeline
from app.ml.inference import MLSignalGenerator
from app.ml.model import ForwardReturnClassifier
from app.strategies.base import Strategy
from app.strategies.context import StrategyContext

logger = logging.getLogger(__name__)


class MLStrategy(Strategy):
    """Wrapa `MLSignalGenerator` num `Strategy` carregado sob demanda.

    Thresholds configuráveis via construtor; quando omitidos, usa os defaults
    do `MLSignalGenerator` (0.6 / 0.4).
    """

    name = "ml_classifier"

    def __init__(
        self,
        *,
        symbol: str,
        model_dir: Path,
        horizon: int = 5,
        pipeline: FeaturePipeline | None = None,
        long_threshold: float = 0.6,
        short_threshold: float = 0.4,
    ) -> None:
        self._symbol = symbol
        self._model_dir = model_dir
        self._horizon = horizon
        self._pipeline = pipeline or default_pipeline()
        self._long_threshold = long_threshold
        self._short_threshold = short_threshold
        self._generator: MLSignalGenerator | None = None
        self._load_attempted: bool = False

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def warmup(self) -> int:
        return 30

    def generate(self, ctx: StrategyContext) -> Signal | None:
        if self._generator is None and not self._ensure_loaded():
            return None
        assert self._generator is not None
        return self._generator.generate(ctx)

    # ------------------------------------------------------------------ internals
    def _ensure_loaded(self) -> bool:
        candidates = sorted(
            self._model_dir.glob(f"{self._symbol}_h{self._horizon}_*.joblib"),
            reverse=True,
        )
        if not candidates:
            if not self._load_attempted:
                logger.debug(
                    "ml_strategy.no_model_yet",
                    extra={"symbol": self._symbol, "model_dir": str(self._model_dir)},
                )
                self._load_attempted = True
            return False

        try:
            model = ForwardReturnClassifier.load(candidates[0])
        except Exception:
            logger.exception(
                "ml_strategy.load_failed",
                extra={"symbol": self._symbol, "path": str(candidates[0])},
            )
            return False

        self._generator = MLSignalGenerator(
            model,
            self._pipeline,
            long_threshold=self._long_threshold,
            short_threshold=self._short_threshold,
        )
        logger.info(
            "ml_strategy.loaded",
            extra={
                "symbol": self._symbol,
                "path": str(candidates[0]),
                "long_threshold": self._long_threshold,
                "short_threshold": self._short_threshold,
            },
        )
        return True