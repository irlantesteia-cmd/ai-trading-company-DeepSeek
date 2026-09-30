from __future__ import annotations

from collections.abc import Callable

from app.core.exceptions import ConfigurationError
from app.strategies.base import Strategy

StrategyFactory = Callable[..., Strategy]


class StrategyRegistry:
    """Catálogo nome → factory de estratégia."""

    def __init__(self) -> None:
        self._factories: dict[str, StrategyFactory] = {}

    def register(self, name: str, factory: StrategyFactory) -> None:
        if name in self._factories:
            raise ConfigurationError(f"Estratégia '{name}' já registrada")
        self._factories[name] = factory

    def create(self, name: str, **params) -> Strategy:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise ConfigurationError(f"Estratégia '{name}' não encontrada") from exc
        return factory(**params)

    def names(self) -> list[str]:
        return sorted(self._factories.keys())


def default_registry() -> StrategyRegistry:
    """Registry com as estratégias built-in já registradas."""
    from app.strategies.mean_reversion import MeanReversionStrategy
    from app.strategies.momentum import MomentumStrategy

    registry = StrategyRegistry()
    registry.register("momentum", MomentumStrategy)
    registry.register("mean_reversion", MeanReversionStrategy)
    return registry