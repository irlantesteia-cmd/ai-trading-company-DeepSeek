from pathlib import Path

import numpy as np
import pytest

from app.ml.model import (
    ForwardReturnClassifier,
    ModelMetadata,
    make_version,
)


def _separable_xy(n: int = 100, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(np.int64)
    return X, y


def test_fit_requires_two_classes():
    X = np.zeros((10, 3))
    y = np.zeros(10, dtype=np.int64)
    with pytest.raises(ValueError):
        ForwardReturnClassifier().fit(X, y)


def test_reproducibility_same_random_state():
    X, y = _separable_xy(200)
    m1 = ForwardReturnClassifier(random_state=7).fit(X, y)
    m2 = ForwardReturnClassifier(random_state=7).fit(X, y)
    p1 = m1.proba_up(X)
    p2 = m2.proba_up(X)
    assert np.allclose(p1, p2)


def test_proba_up_shape_and_range():
    X, y = _separable_xy(150)
    m = ForwardReturnClassifier(random_state=1).fit(X, y)
    p = m.proba_up(X)
    assert p.shape == (150,)
    assert (p >= 0).all() and (p <= 1).all()


def test_save_and_load_roundtrip(tmp_path: Path):
    X, y = _separable_xy(100)
    m = ForwardReturnClassifier(random_state=3).fit(X, y)
    meta = ModelMetadata(
        version="BTCUSDT_h5_test",
        symbol="BTCUSDT",
        horizon=5,
        feature_names=["a", "b", "c", "d"],
        trained_at="2025-01-01T00:00:00Z",
        n_train=80,
        n_test=20,
        metrics={"accuracy": 0.9},
        hyperparams={},
    )
    path = tmp_path / "model.joblib"
    m.save(path, meta)
    assert path.exists()
    assert path.with_suffix(".json").exists()

    loaded = ForwardReturnClassifier.load(path)
    assert np.allclose(m.proba_up(X), loaded.proba_up(X))


def test_make_version_format():
    v = make_version("BTCUSDT", 5)
    assert v.startswith("BTCUSDT_h5_")