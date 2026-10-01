from pathlib import Path

import pytest

from app.core.exceptions import ConfigurationError
from app.strategies.factory import make_strategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.ml_strategy import MLStrategy
from app.strategies.momentum import MomentumStrategy


def _make(spec: str):
    return make_strategy(spec, symbol="BTCUSDT", model_dir=Path("/tmp/models"))


def test_momentum():
    assert isinstance(_make("momentum"), MomentumStrategy)


def test_mean_reversion():
    assert isinstance(_make("mean_reversion"), MeanReversionStrategy)


def test_ml():
    s = _make("ml")
    assert isinstance(s, MLStrategy)
    assert s.symbol == "BTCUSDT"


def test_none():
    assert _make("none") is None


def test_case_insensitive_and_trimmed():
    assert isinstance(_make("  MOMENTUM  "), MomentumStrategy)
    assert isinstance(_make("ML"), MLStrategy)


def test_unknown_spec_raises():
    with pytest.raises(ConfigurationError, match="strategy spec inválida"):
        _make("bogus")


def test_ml_strategy_horizon_passed():
    s = make_strategy(
        "ml", symbol="ETHUSDT", model_dir=Path("/tmp"), horizon=15
    )
    assert isinstance(s, MLStrategy)
    assert s.symbol == "ETHUSDT"