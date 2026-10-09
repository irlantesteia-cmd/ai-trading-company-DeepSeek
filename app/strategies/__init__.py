"""Estratégias da AI Trading Company.

Imports eager: os ciclos que exigiam re-exports lazy (PEP 562) foram
eliminados movendo `indicators` para `app.core` e `StrategyContext` para
`app.domain.models`, módulos que não dependem de `app.strategies`.
`tests/unit/test_import_cycles.py` protege contra regressões.
"""

from __future__ import annotations

from app.domain.models.strategy_context import StrategyContext
from app.strategies.base import Strategy
from app.strategies.factory import make_strategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.ml_strategy import MLStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.registry import StrategyRegistry, default_registry

__all__ = [
    "MLStrategy",
    "MeanReversionStrategy",
    "MomentumStrategy",
    "Strategy",
    "StrategyContext",
    "StrategyRegistry",
    "default_registry",
    "make_strategy",
]
