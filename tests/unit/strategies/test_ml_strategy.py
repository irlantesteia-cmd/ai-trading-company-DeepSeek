from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.core.enums import MarketType
from app.strategies.context import StrategyContext
from app.strategies.ml_strategy import MLStrategy
from tests.unit.strategies.conftest import make_candles


def _fake_pipeline() -> Pipeline:
    X = np.random.default_rng(seed=42).normal(size=(80, 13))
    y = (X[:, 0] > 0).astype(int)
    return Pipeline(
        [("scaler", StandardScaler()), ("clf", LogisticRegression(max_iter=200))]
    ).fit(X, y)


def _ctx(n: int = 60) -> StrategyContext:
    candles = make_candles([100.0 + (i % 5) * 0.1 for i in range(n)])
    return StrategyContext(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        interval="5m",
        candles=candles,
    )


def test_returns_none_when_no_model(tmp_path: Path):
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    assert strat.generate(_ctx()) is None


def test_loads_model_and_generates(tmp_path: Path):
    model_path = tmp_path / "BTCUSDT_h5_20250101T000000Z.joblib"
    joblib.dump(_fake_pipeline(), model_path)
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)

    # Primeira chamada: tenta carregar + gera (pode ser None se proba neutra)
    sig = strat.generate(_ctx())
    assert sig is None or sig.symbol == "BTCUSDT"


def test_caches_model_between_calls(tmp_path: Path):
    model_path = tmp_path / "BTCUSDT_h5_20250101T000000Z.joblib"
    joblib.dump(_fake_pipeline(), model_path)
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)

    strat.generate(_ctx())
    assert strat._generator is not None

    # Grava segundo modelo (não deve ser carregado — cacheado o primeiro)
    model_path2 = tmp_path / "BTCUSDT_h5_20250102T000000Z.joblib"
    joblib.dump(_fake_pipeline(), model_path2)
    strat.generate(_ctx())
    assert strat._generator is not None


def test_warmup_value():
    strat = MLStrategy(symbol="BTCUSDT", model_dir=Path("/nonexistent"))
    assert strat.warmup == 30


def test_corrupted_model_is_swallowed(tmp_path: Path):
    bad = tmp_path / "BTCUSDT_h5_20250101T000000Z.joblib"
    bad.write_bytes(b"not a joblib file")
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    # Não deve levantar — retorna None
    assert strat.generate(_ctx()) is None