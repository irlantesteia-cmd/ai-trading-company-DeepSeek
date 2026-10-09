from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import BaseAgent
from app.core.config import settings
from app.core.enums import AgentRole, MarketType
from app.database.repositories.candle import CandleRepository
from app.domain.models.market import Candle
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.features.cross_asset import CrossAssetPipeline
from app.features.pipeline import FeaturePipeline, FeatureTransformer, default_pipeline
from app.ml.dataset import build_dataset
from app.ml.inference import MLSignalGenerator
from app.ml.model import ForwardReturnClassifier, make_version
from app.ml.training import (
    TrainingResult,
    save_training_result,
    train_walk_forward,
)

logger = logging.getLogger(__name__)


class MLAgent(BaseAgent):
    """Agente de ML: treina, avalia, versiona e gera sinais.

    `cross_asset_refs`: mapa símbolo → ref (ex.: `{"SOLUSDT": "BTCUSDT"}`).
    Para símbolos com ref, o pipeline é envelopado em `CrossAssetPipeline`
    e o treino carrega candles do ref automaticamente.
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
        min_deploy_auc: float | None = None,
        cross_asset_refs: dict[str, str] | None = None,
        cross_asset_horizons: list[int] | None = None,
    ) -> None:
        super().__init__(context)
        self._model_dir = model_dir or Path("models")
        self._base_pipeline = pipeline or default_pipeline()
        self._horizon = horizon
        self._min_deploy_auc = (
            min_deploy_auc
            if min_deploy_auc is not None
            else settings.ml_min_deploy_auc
        )
        self._cross_asset_refs = dict(cross_asset_refs or {})
        self._cross_asset_horizons = list(
            cross_asset_horizons or settings.ml_cross_asset_ref_horizons
        )
        self._models: dict[str, ForwardReturnClassifier] = {}
        self._generators: dict[str, MLSignalGenerator] = {}

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    @property
    def horizon(self) -> int:
        return self._horizon

    @property
    def min_deploy_auc(self) -> float:
        return self._min_deploy_auc

    def _effective_pipeline(self, symbol: str) -> FeatureTransformer:
        ref = self._cross_asset_refs.get(symbol)
        if not ref:
            return self._base_pipeline
        return CrossAssetPipeline(
            self._base_pipeline,
            ref_symbol=ref,
            ref_horizons=self._cross_asset_horizons,
        )

    # ------------------------------------------------------------------ train
    async def train(
        self,
        *,
        symbol: str,
        candles: list[Candle] | None = None,
        market_type: MarketType = MarketType.FUTURES,
        interval: str = "5m",
        limit: int = 500,
        random_state: int = 42,
        C: float = 1.0,
    ) -> TrainingResult:
        if candles is None:
            candles = await self._load_candles_from_db(
                symbol=symbol,
                market_type=market_type,
                interval=interval,
                limit=limit,
            )

        if len(candles) < 50:
            raise ValueError(
                f"candles insuficientes para treino: {len(candles)} "
                f"(mínimo 50; rode o backfill primeiro)"
            )

        pipeline = self._effective_pipeline(symbol)

        ref_candles: dict[str, list[Candle]] | None = None
        ref_symbol = self._cross_asset_refs.get(symbol)
        if ref_symbol:
            ref = await self._load_candles_from_db(
                symbol=ref_symbol,
                market_type=market_type,
                interval=interval,
                limit=limit,
            )
            if not ref:
                raise ValueError(
                    f"candles de referência vazios para {ref_symbol} "
                    f"(necessários para treinar {symbol})"
                )
            ref_candles = {ref_symbol: ref}

        dataset = build_dataset(
            candles,
            pipeline=pipeline,
            horizon=self._horizon,
            min_return_pct=settings.ml_label_min_return_pct,
            ref_candles=ref_candles,
        )
        result = train_walk_forward(
            dataset,
            symbol=symbol,
            n_folds=settings.ml_walk_forward_folds,
            min_train_size=settings.ml_walk_forward_min_train,
            random_state=random_state,
            C=C,
            min_deploy_auc=self._min_deploy_auc,
            max_std=settings.ml_walk_forward_max_std,
        )
        path = save_training_result(result, self._model_dir)
        self._models[symbol] = result.model
        self._generators[symbol] = MLSignalGenerator(result.model, pipeline)

        logger.info(
            "ml.trained",
            extra={
                "symbol": symbol,
                "version": result.metadata.version,
                "n_train": result.metadata.n_train,
                "n_test": result.metadata.n_test,
                "test_accuracy": result.test_accuracy,
                "auc": result.test_metrics.get("auc"),
                "wf_mean_auc": result.test_metrics.get("wf_mean_auc"),
                "wf_std_auc": result.test_metrics.get("wf_std_auc"),
                "wf_min_auc": result.test_metrics.get("wf_min_auc"),
                "wf_num_folds": result.test_metrics.get("wf_num_folds"),
                "baseline_accuracy": result.test_metrics.get("baseline_accuracy"),
                "baseline_auc": result.test_metrics.get("baseline_auc"),
                "deployable": result.metadata.deployable,
                "deploy_reason": result.metadata.deploy_reason,
                "label_min_return_pct": dataset.min_return_pct,
                "num_features": len(pipeline.names),
                "ref_symbol": ref_symbol,
                "path": str(path),
            },
        )
        return result

    async def _load_candles_from_db(
        self,
        *,
        symbol: str,
        market_type: MarketType,
        interval: str,
        limit: int,
    ) -> list[Candle]:
        if self.context.session_factory is None:
            raise RuntimeError(
                "session_factory ausente no AgentContext; "
                "injete candles=... ou configure o contexto"
            )
        async with self.context.session_factory() as session:
            repo = CandleRepository(session)
            rows = await repo.get_recent(
                symbol=symbol,
                market_type=market_type,
                interval=interval,
                limit=limit,
            )

        return [
            Candle(
                symbol=r.symbol,
                market_type=MarketType(r.market_type),
                interval=r.interval,
                open_time=r.open_time,
                close_time=r.close_time,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                trades=r.trades,
                taker_buy_base_volume=r.taker_buy_base_volume,
                closed=True,
            )
            for r in rows
        ]

    # ---------------------------------------------------------------- predict
    async def predict(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        market_type: MarketType = MarketType.FUTURES,
        interval: str = "5m",
        ref_candles: dict[str, list[Candle]] | None = None,
    ) -> Signal | None:
        generator = self._generators.get(symbol)
        if generator is None:
            model = self._load_latest(symbol)
            if model is None:
                logger.warning("ml.no_model", extra={"symbol": symbol})
                return None
            generator = MLSignalGenerator(model, self._effective_pipeline(symbol))
            self._generators[symbol] = generator

        ctx = StrategyContext(
            symbol=symbol,
            market_type=market_type,
            interval=interval,
            candles=candles,
            ref_candles=ref_candles or {},
        )
        return generator.generate(ctx)

    # ----------------------------------------------------------------- helpers
    def _load_latest(self, symbol: str) -> ForwardReturnClassifier | None:
        if not self._model_dir.exists():
            return None
        candidates = sorted(
            self._model_dir.glob(f"{symbol}_h{self._horizon}_*.joblib"),
            reverse=True,
        )
        expected_features = self._effective_pipeline(symbol).names
        for path in candidates:
            metadata = ForwardReturnClassifier.load_metadata(path)
            if metadata is None or not metadata.deployable:
                continue
            if metadata.feature_names != expected_features:
                continue
            auc = metadata.metrics.get("auc")
            if auc is None or auc < self._min_deploy_auc:
                continue
            try:
                return ForwardReturnClassifier.load(path)
            except Exception:
                logger.exception("ml.load_failed", extra={"path": str(path)})
                continue
        return None

    @staticmethod
    def preview_version(symbol: str, horizon: int) -> str:
        return make_version(symbol, horizon)