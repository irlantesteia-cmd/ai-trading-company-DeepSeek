import numpy as np

from app.features.pipeline import default_pipeline
from app.ml.dataset import build_dataset, temporal_split
from tests.unit.strategies.conftest import make_candles


def _candles(n: int = 200):
    # Tendência suave com ruído determinístico (sem randomness)
    closes = [100.0 + 0.1 * i + (1.0 if i % 3 == 0 else -0.5) for i in range(n)]
    return make_candles(closes)


def test_dataset_alignment_no_leakage():
    candles = _candles(200)
    pipeline = default_pipeline()
    ds = build_dataset(candles, pipeline=pipeline, horizon=5)
    assert len(ds) > 0
    assert ds.X.shape == (len(ds), len(ds.feature_names))
    assert ds.y.shape == (len(ds),)
    # Primeiro índice deve ter candles suficientes para features + horizon
    assert ds.indices[0] >= 10
    # Último índice precisa deixar margem para o horizonte
    assert ds.indices[-1] + ds.horizon < len(candles)


def test_dataset_labels_match_forward_return():
    candles = _candles(120)
    pipeline = default_pipeline()
    ds = build_dataset(candles, pipeline=pipeline, horizon=3)
    for j, idx in enumerate(ds.indices):
        fwd = float(candles[idx + 3].close) - float(candles[idx].close)
        expected = 1 if fwd > 0 else 0
        assert int(ds.y[j]) == expected


def test_dataset_empty_when_too_short():
    candles = _candles(5)
    pipeline = default_pipeline()
    ds = build_dataset(candles, pipeline=pipeline, horizon=3)
    assert len(ds) == 0


def test_temporal_split_preserves_order():
    candles = _candles(200)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=5)
    train, test = temporal_split(ds, test_size=0.25)
    assert len(train) + len(test) == len(ds)
    assert train.times[-1] < test.times[0]
    # Nenhuma amostra deve estar em ambos
    assert set(train.indices).isdisjoint(set(test.indices))


def test_temporal_split_rejects_invalid_test_size():
    candles = _candles(60)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    import pytest

    with pytest.raises(ValueError):
        temporal_split(ds, test_size=0.0)
    with pytest.raises(ValueError):
        temporal_split(ds, test_size=1.0)


def test_dataset_x_dtype():
    candles = _candles(100)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=5)
    assert ds.X.dtype == np.float64
    assert ds.y.dtype == np.int64