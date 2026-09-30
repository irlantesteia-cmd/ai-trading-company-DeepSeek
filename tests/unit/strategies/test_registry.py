import pytest

from app.core.exceptions import ConfigurationError
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.registry import StrategyRegistry, default_registry


def test_register_and_create():
    reg = StrategyRegistry()
    reg.register("m", MomentumStrategy)
    s = reg.create("m", fast_period=3, slow_period=5)
    assert isinstance(s, MomentumStrategy)
    assert s.fast_period == 3


def test_duplicate_register_raises():
    reg = StrategyRegistry()
    reg.register("m", MomentumStrategy)
    with pytest.raises(ConfigurationError):
        reg.register("m", MomentumStrategy)


def test_create_missing_raises():
    reg = StrategyRegistry()
    with pytest.raises(ConfigurationError):
        reg.create("nope")


def test_default_registry_has_both():
    reg = default_registry()
    assert reg.names() == ["mean_reversion", "momentum"]
    assert isinstance(reg.create("momentum"), MomentumStrategy)
    assert isinstance(reg.create("mean_reversion"), MeanReversionStrategy)