"""Estratégias da AI Trading Company.

Re-exports são expostos via `__getattr__` (PEP 562) para `MLStrategy` e
`make_strategy`. Importá-los eager no topo criaria um ciclo:

    features.engineering → strategies.indicators
      → strategies/__init__ (eager) → factory
        → features.cross_asset → features.pipeline → features.engineering

Como `features.engineering` é o primeiro módulo a importar
`strategies.indicators`, ele dispara `strategies/__init__` antes de
`features` estar pronto. Adiar `factory` e `ml_strategy` quebra o ciclo
sem custo (o primeiro `from app.strategies import make_strategy` paga o
import — que já seria pago em qualquer boot real).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from app.strategies.base import Strategy
from app.strategies.context import StrategyContext
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.registry import StrategyRegistry, default_registry

if TYPE_CHECKING:
    from app.strategies.factory import make_strategy
    from app.strategies.ml_strategy import MLStrategy

_LAZY_EXPORTS: dict[str, str] = {
    "MLStrategy": "app.strategies.ml_strategy",
    "make_strategy": "app.strategies.factory",
}

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


def __getattr__(name: str) -> object:
    """Lazy re-export via PEP 562. Só resolve `MLStrategy`/`make_strategy`."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    module = importlib.import_module(target)
    value = getattr(module, name)
    globals()[name] = value
    return value