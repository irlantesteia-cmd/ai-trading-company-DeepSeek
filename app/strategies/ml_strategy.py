"""Estratégia que delega ao modelo ML treinado do símbolo.

Carregamento **lazy** e **reativo**: o modelo é lido do disco na primeira
chamada a `generate()` e re-verificado a cada chamada. Se aparecer um
modelo mais novo deployável, o loader troca automaticamente.

Gate de deploy (quádruplo):
  1. `metadata.deployable` — decisão congelada no momento do treino.
  2. `metadata.trained_at >= boot_time - ml_boot_model_grace_seconds` —
     evita usar modelo de sessão anterior antes do autotrain do boot
     atual terminar.
  3. `metadata.feature_names == pipeline.names` — bloqueia modelos
     treinados com um pipeline de features diferente. Sem isso, um
     `.joblib` de 13 features seria carregado num pipeline de 8 e
     quebraria na hora da predição (ou pior, daria resultado silencioso
     errado).
  4. `metadata.metrics.auc >= settings.ml_min_deploy_auc` — decisão
     corrente de AUC.

Os quatro precisam passar. Modelos que falham ficam no disco para
auditoria, mas não geram sinal.

O `pipeline` pode ser um `FeaturePipeline` puro (8 features) ou um
`CrossAssetPipeline` (8 + N cross-asset, ML-3d-1). Nesse segundo caso,
o `AssetAgent` precisa popular `StrategyContext.ref_candles` — o gate 3
garante que o modelo carregado só seja usado se tiver sido treinado com
exatamente esse conjunto de features.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import settings
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.features.pipeline import FeatureTransformer, default_pipeline
from app.ml.inference import MLSignalGenerator
from app.ml.model import ForwardReturnClassifier
from app.strategies.base import Strategy

logger = logging.getLogger(__name__)


class MLStrategy(Strategy):
    """Wrapa `MLSignalGenerator` num `Strategy` carregado sob demanda.

    Thresholds configuráveis via construtor; quando omitidos, usa os
    defaults do `MLSignalGenerator` (0.6 / 0.4).
    """

    name = "ml_classifier"

    def __init__(
        self,
        *,
        symbol: str,
        model_dir: Path,
        horizon: int = 5,
        pipeline: FeatureTransformer | None = None,
        long_threshold: float = 0.6,
        short_threshold: float = 0.4,
        min_deploy_auc: float | None = None,
        boot_grace_seconds: float | None = None,
    ) -> None:
        self._symbol = symbol
        self._model_dir = model_dir
        self._horizon = horizon
        self._pipeline = pipeline or default_pipeline()
        self._long_threshold = long_threshold
        self._short_threshold = short_threshold
        self._min_deploy_auc = (
            min_deploy_auc
            if min_deploy_auc is not None
            else settings.ml_min_deploy_auc
        )
        grace = (
            boot_grace_seconds
            if boot_grace_seconds is not None
            else settings.ml_boot_model_grace_seconds
        )
        self._boot_floor = datetime.now(UTC) - timedelta(seconds=grace)
        self._generator: MLSignalGenerator | None = None
        self._loaded_path: Path | None = None
        self._no_deployable_logged: bool = False

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def warmup(self) -> int:
        return 30

    @property
    def min_deploy_auc(self) -> float:
        return self._min_deploy_auc

    @property
    def boot_floor(self) -> datetime:
        return self._boot_floor

    def generate(self, ctx: StrategyContext) -> Signal | None:
        if not self._ensure_generator():
            return None
        assert self._generator is not None
        return self._generator.generate(ctx)

    # ------------------------------------------------------------------ internals
    def _ensure_generator(self) -> bool:
        newest = self._find_newest_deployable()

        if newest is None:
            if self._generator is not None:
                logger.warning(
                    "ml_strategy.generator_dropped",
                    extra={
                        "symbol": self._symbol,
                        "previous_path": str(self._loaded_path),
                    },
                )
                self._generator = None
                self._loaded_path = None
            if not self._no_deployable_logged:
                logger.warning(
                    "ml_strategy.no_deployable_model",
                    extra={
                        "symbol": self._symbol,
                        "min_deploy_auc": self._min_deploy_auc,
                        "boot_floor": self._boot_floor.isoformat(),
                        "expected_features": self._pipeline.names,
                    },
                )
                self._no_deployable_logged = True
            return False

        if newest == self._loaded_path and self._generator is not None:
            return True

        try:
            model = ForwardReturnClassifier.load(newest)
        except Exception:
            logger.exception(
                "ml_strategy.load_failed",
                extra={"symbol": self._symbol, "path": str(newest)},
            )
            return False

        self._generator = MLSignalGenerator(
            model,
            self._pipeline,
            long_threshold=self._long_threshold,
            short_threshold=self._short_threshold,
        )
        self._loaded_path = newest
        self._no_deployable_logged = False
        logger.info(
            "ml_strategy.loaded",
            extra={
                "symbol": self._symbol,
                "path": str(newest),
                "long_threshold": self._long_threshold,
                "short_threshold": self._short_threshold,
                "min_deploy_auc": self._min_deploy_auc,
                "num_features": len(self._pipeline.names),
            },
        )
        return True

    def _find_newest_deployable(self) -> Path | None:
        candidates = sorted(
            self._model_dir.glob(f"{self._symbol}_h{self._horizon}_*.joblib"),
            reverse=True,
        )
        expected_features = self._pipeline.names
        for path in candidates:
            metadata = ForwardReturnClassifier.load_metadata(path)
            if metadata is None:
                continue

            # Gate 1: decisão congelada no treino.
            if not metadata.deployable:
                logger.debug(
                    "ml_strategy.skip_not_deployable",
                    extra={
                        "symbol": self._symbol,
                        "path": str(path),
                        "reason": metadata.deploy_reason,
                        "gate": "metadata",
                    },
                )
                continue

            # Gate 2: modelo precisa ser da sessão atual (pós boot floor).
            trained_at = self._parse_trained_at(metadata.trained_at)
            if trained_at is None:
                logger.warning(
                    "ml_strategy.invalid_trained_at",
                    extra={
                        "symbol": self._symbol,
                        "path": str(path),
                        "trained_at": metadata.trained_at,
                    },
                )
                continue
            if trained_at < self._boot_floor:
                logger.debug(
                    "ml_strategy.skip_pre_session",
                    extra={
                        "symbol": self._symbol,
                        "path": str(path),
                        "trained_at": metadata.trained_at,
                        "boot_floor": self._boot_floor.isoformat(),
                        "gate": "boot_floor",
                    },
                )
                continue

            # Gate 3: pipeline de features compatível.
            if metadata.feature_names != expected_features:
                logger.info(
                    "ml_strategy.skip_feature_mismatch",
                    extra={
                        "symbol": self._symbol,
                        "path": str(path),
                        "model_features": metadata.feature_names,
                        "expected_features": expected_features,
                        "gate": "feature_compat",
                    },
                )
                continue

            # Gate 4: decisão corrente de AUC.
            auc = metadata.metrics.get("auc")
            if auc is None or auc < self._min_deploy_auc:
                logger.debug(
                    "ml_strategy.skip_auc_below_current_threshold",
                    extra={
                        "symbol": self._symbol,
                        "path": str(path),
                        "auc": auc,
                        "min_deploy_auc": self._min_deploy_auc,
                        "gate": "current_threshold",
                    },
                )
                continue

            return path
        return None

    @staticmethod
    def _parse_trained_at(value: str) -> datetime | None:
        """Parseia `trained_at` (ISO-8601 UTC) com fallback para o formato
        sem timezone (`+00:00`). Retorna `None` se inválido (fail-closed).
        """
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
        except (ValueError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed