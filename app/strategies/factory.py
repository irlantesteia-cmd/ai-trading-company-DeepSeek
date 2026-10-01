"""Fábrica de estratégias a partir de uma spec textual.

Specs suportadas:
    "momentum"        → MomentumStrategy()
    "mean_reversion"  → MeanReversionStrategy()
    "ml"              → MLStrategy(symbol=..., model_dir=..., horizon=...)
    "none"            → None (AssetAgent fica inerte para o símbolo)

Qualquer outra spec levanta `ConfigurationError` — falha rápida em config
errada evita silêncio confuso em produção.
"""

from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.strategies.base import Strategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.ml_strategy import MLStrategy
from app.strategies.momentum import MomentumStrategy

_KNOWN_SPECS = {"momentum", "mean_reversion", "ml", "none"}


def make_strategy(
    spec: str,
    *,
    symbol: str,
    model_dir: Path,
    horizon: int = 5,
) -> Strategy | None:
    """Resolve uma spec textual para uma instância de `Strategy` (ou None).

    Case-insensitive, com trim. Specs desconhecidas levantam
    `ConfigurationError` listando as válidas.
    """
    normalized = spec.strip().lower()

    if normalized == "none":
        return None
    if normalized == "momentum":
        return MomentumStrategy()
    if normalized == "mean_reversion":
        return MeanReversionStrategy()
    if normalized == "ml":
        return MLStrategy(symbol=symbol, model_dir=model_dir, horizon=horizon)

    raise ConfigurationError(
        f"strategy spec inválida: {spec!r}. "
        f"Válidas: {sorted(_KNOWN_SPECS)}"
    )