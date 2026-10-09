import numpy as np
import pytest

from app.core.enums import MarketType, SignalDirection
from app.domain.models.strategy_context import StrategyContext
from app.features.pipeline import default_pipeline
from app.ml.inference import MLSignalGenerator
from tests.unit.strategies.conftest import make_candles


class _StubModel:
    """Modelo que devolve uma probabilidade fixa — só para testar o mapeamento."""

    def __init__(self, proba: float) -> None:
        self._p = proba

    def proba_up(self, X):
        return np.full(X.shape[0], self._p)


def _ctx(n: int = 80) -> StrategyContext:
    candles = make_candles([100.0 + i * 0.05 for i in range(n)])
    return StrategyContext(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        candles=candles,
    )


def _gen(proba: float) -> MLSignalGenerator:
    return MLSignalGenerator(
        _StubModel(proba),
        default_pipeline(),
        long_threshold=0.6,
        short_threshold=0.4,
    )


def test_long_when_proba_high():
    sig = _gen(0.75).generate(_ctx())
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.confidence == pytest.approx(0.75)
    assert sig.suggested_entry is not None
    assert sig.suggested_stop is not None
    assert sig.suggested_target is not None
    assert sig.suggested_stop < sig.suggested_entry < sig.suggested_target


def test_short_when_proba_low():
    sig = _gen(0.25).generate(_ctx())
    assert sig is not None
    assert sig.direction == SignalDirection.SHORT
    assert sig.confidence == pytest.approx(0.75)
    assert sig.suggested_stop > sig.suggested_entry > sig.suggested_target


def test_none_when_proba_middle():
    sig = _gen(0.5).generate(_ctx())
    assert sig is None


def test_invalid_thresholds_raise():
    with pytest.raises(ValueError):
        MLSignalGenerator(
            _StubModel(0.5),
            default_pipeline(),
            long_threshold=0.4,
            short_threshold=0.6,
        )


def test_generator_name_and_agent():
    gen = _gen(0.9)
    sig = gen.generate(_ctx())
    assert sig is not None
    assert sig.strategy == "ml_classifier"
    assert sig.agent == "ml::ml_classifier"


def test_empty_candles_returns_none():
    gen = _gen(0.9)
    ctx = StrategyContext(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        candles=[],
    )
    assert gen.generate(ctx) is None