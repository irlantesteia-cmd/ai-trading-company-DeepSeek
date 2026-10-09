"""Fábrica de estratégias a partir de uma spec textual.

Specs suportadas:
    "momentum"        → MomentumStrategy()
    "mean_reversion"  → MeanReversionStrategy()
    "ml"              → MLStrategy(symbol=..., model_dir=..., horizon=...)
                         Envelopado em CrossAssetPipeline quando `ref_symbol`
                         é fornecido (ML-3d-1).
    "none"            → None (AssetAgent fica inerte para o símbolo)

Qualquer outra spec levanta `ConfigurationError`.
"""

from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.features.cross_asset import CrossAssetPipeline
from app.features.pipeline import FeatureTransformer, default_pipeline
from app.strategies.base import Strategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.ml_strategy import MLStrategy
from app.strategies.momentum import MomentumStrategy

_KNOWN_SPECS = {"momentum", "mean_reversion", "ml", "none"}
_DEFAULT_REF_HORIZONS: list[int] = [1, 3, 5]


def make_strategy(
    spec: str,
    *,
    symbol: str,
    model_dir: Path,
    horizon: int = 5,
    ml_long_threshold: float = 0.6,
    ml_short_threshold: float = 0.4,
    ref_symbol: str | None = None,
    ref_horizons: list[int] | None = None,
) -> Strategy | None:
    """Resolve uma spec textual para uma instância de `Strategy` (ou None).

    Quando `spec == "ml"` e `ref_symbol` é fornecido, o `FeaturePipeline`
    base (8 features) é envelopado em `CrossAssetPipeline`, adicionando
    features `{ref}_return_{h}` para cada `h` em `ref_horizons`. O
    `MLStrategy` recebe esse pipeline envelopado — e o gate de feature
    compat (Gate 3 do `MLStrategy`) garantirá que só modelos treinados
    com o MESMO pipeline (mesmos `feature_names`) sejam usados na
    inferência. Isso impede que um `.joblib` de 8 features seja carregado
    num pipeline de 11, ou vice-versa.
    """
    normalized = spec.strip().lower()

    if normalized == "none":
        return None
    if normalized == "momentum":
        return MomentumStrategy()
    if normalized == "mean_reversion":
        return MeanReversionStrategy()
    if normalized == "ml":
        base = default_pipeline()
        pipeline: FeatureTransformer = base
        if ref_symbol:
            horizons = sorted(set(ref_horizons or _DEFAULT_REF_HORIZONS))
            pipeline = CrossAssetPipeline(
                base,
                ref_symbol=ref_symbol,
                ref_horizons=horizons,
            )
        return MLStrategy(
            symbol=symbol,
            model_dir=model_dir,
            horizon=horizon,
            pipeline=pipeline,
            long_threshold=ml_long_threshold,
            short_threshold=ml_short_threshold,
        )

    raise ConfigurationError(
        f"strategy spec inválida: {spec!r}. "
        f"Válidas: {sorted(_KNOWN_SPECS)}"
    )