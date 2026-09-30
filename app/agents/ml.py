from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import BaseAgent
from app.core.enums import AgentRole, MarketType
from app.domain.models.market import Candle
from app.domain.models.signal import Signal
from app.features.pipeline import FeaturePipeline, default_pipeline
from app.ml.dataset import build_dataset
from app.ml.inference import MLSignalGenerator
from app.ml.model import ForwardReturnClassifier, make_version
from app.ml.training import TrainingResult, save_training_result, train_classifier
from app.strategies.context import StrategyContext

logger = logging.getLogger(__name__)


class MLAgent(BaseAgent):
    """Agente de ML: treina/avalia/versiona modelos e gera sinais via MLSignalGenerator.

    O diretório padrão é `models/` (gitignored). Cada modelo é salvo como
    `<version>.joblib` + `<version>.json` (metadados).
    """

    role = AgentRole.ML
    name = "ml"

    def __init__(
        self,
        context,
        *,
        model_dir: Path | None = None,
        pipeline: FeaturePipeline | None = None,
        horizon: int = 5,
    ) -> None:
        super().__init__(context)
        self._model_dir = model_dir or Path("models")
        self._pipeline = pipeline or default_pipeline()
        self._horizon = horizon
        self._models: dict[str, ForwardReturnClassifier] = {}
        self._generators: dict[str, MLSignalGenerator] = {}

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    @property
    def horizon(self) -> int:
        return self._horizon

    # ------------------------------------------------------------------ train
    async def train(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        test_size: float = 0.2,
        random_state: int = 42,
        C: float = 1.0,
    ) -> TrainingResult:
        dataset = build_dataset(
            candles, pipeline=self._pipeline, horizon=self._horizon
        )
        result = train_classifier(
            dataset,
            symbol=symbol,
            test_size=test_size,
            random_state=random_state,
            C=C,
        )
        path = save_training_result(result, self._model_dir)
        self._models[symbol] = result.model
        self._generators[symbol] = self._make_generator(result.model)

        logger.info(
            "ml.trained",
            extra={
                "symbol": symbol,
                "version": result.metadata.version,
                "n_train": result.metadata.n_train,
                "n_test": result.metadata.n_test,
                "test_accuracy": result.test_accuracy,
                "auc": result.test_metrics.get("auc"),
                "path": str(path),
            },
        )
        return result

    # ---------------------------------------------------------------- predict
    async def predict(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        market_type: MarketType = MarketType.FUTURES,
        interval: str = "5m",
    ) -> Signal | None:
        generator = self._generators.get(symbol)
        if generator is None:
            model = self._load_latest(symbol)
            if model is None:
                logger.warning("ml.no_model", extra={"symbol": symbol})
                return None
            generator = self._make_generator(model)
            self._generators[symbol] = generator

        ctx = StrategyContext(
            symbol=symbol,
            market_type=market_type,
            interval=interval,
            candles=candles,
        )
        return generator.generate(ctx)

    # ----------------------------------------------------------------- helpers
    def _make_generator(self, model: ForwardReturnClassifier) -> MLSignalGenerator:
        return MLSignalGenerator(model, self._pipeline)

    def _load_latest(self, symbol: str) -> ForwardReturnClassifier | None:
        if not self._model_dir.exists():
            return None
        candidates = sorted(
            self._model_dir.glob(f"{symbol}_h{self._horizon}_*.joblib"),
            reverse=True,
        )
        if not candidates:
            return None
        try:
            return ForwardReturnClassifier.load(candidates[0])
        except Exception:
            logger.exception("ml.load_failed", extra={"path": str(candidates[0])})
            return None

    @staticmethod
    def preview_version(symbol: str, horizon: int) -> str:
        return make_version(symbol, horizon)