from pathlib import Path

import numpy as np
import pytest

from app.features.pipeline import default_pipeline
from app.ml.dataset import build_dataset
from app.ml.training import save_training_result, train_classifier
from tests.unit.strategies.conftest import make_candles


def _trending_candles(n: int = 300):
    # Série com tendência clara e alternância: o modelo tem sinal real para aprender.
    closes = []
    price = 100.0
    for i in range(n):
        price += 0.5 if (i // 20) % 2 == 0 else -0.4
        closes.append(price)
    return make_candles(closes)


def test_training_end_to_end_produces_metrics():
    candles = _trending_candles(300)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    assert len(ds) >= 50
    result = train_classifier(ds, symbol="BTCUSDT", test_size=0.25, random_state=42)

    assert result.model.is_fitted
    assert result.metadata.symbol == "BTCUSDT"
    # Partição completa: treino + teste + purgadas == dataset.
    assert (
        result.metadata.n_train + result.metadata.n_test + result.metadata.n_purged
        == len(ds)
    )
    assert result.metadata.n_purged == ds.horizon
    assert 0.0 <= result.test_accuracy <= 1.0
    assert "auc" in result.test_metrics
    assert "baseline_auc" in result.test_metrics
    assert "baseline_accuracy" in result.test_metrics


def test_training_reproducible():
    candles = _trending_candles(300)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    r1 = train_classifier(ds, symbol="BTCUSDT", random_state=99)
    r2 = train_classifier(ds, symbol="BTCUSDT", random_state=99)
    assert r1.test_accuracy == r2.test_accuracy
    assert np.allclose(r1.model.proba_up(ds.X), r2.model.proba_up(ds.X))


def test_training_rejects_tiny_dataset():
    candles = _trending_candles(40)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    with pytest.raises(ValueError):
        train_classifier(ds, symbol="BTCUSDT")


def test_save_training_result(tmp_path: Path):
    candles = _trending_candles(300)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    result = train_classifier(ds, symbol="BTCUSDT", random_state=1)
    path = save_training_result(result, tmp_path)
    assert path.exists()
    assert path.with_suffix(".json").exists()