"""Testes da fase ML-2b-1: gate de deploy por AUC."""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np

from app.features.pipeline import default_pipeline
from app.ml.dataset import Dataset, build_dataset
from app.ml.model import ForwardReturnClassifier, ModelMetadata
from app.ml.training import (
    _compute_deployable,
    save_training_result,
    train_classifier,
)
from app.strategies.context import StrategyContext
from app.strategies.ml_strategy import MLStrategy
from tests.unit.strategies.conftest import make_candles


# ---------------------------------------------------------------- _compute_deployable
def test_compute_deployable_above_min() -> None:
    ok, reason = _compute_deployable(0.60, 0.50)
    assert ok is True
    assert reason is None


def test_compute_deployable_below_min() -> None:
    ok, reason = _compute_deployable(0.45, 0.50)
    assert ok is False
    assert reason is not None
    assert "auc_below_min" in reason


def test_compute_deployable_equal_to_min() -> None:
    # AUC == min é aceito (>=). Modelo com 0.5 exato passa o gate;
    # quem quiser ser mais estrito, sobe `min_deploy_auc`.
    ok, reason = _compute_deployable(0.50, 0.50)
    assert ok is True
    assert reason is None


def test_compute_deployable_nan() -> None:
    ok, reason = _compute_deployable(float("nan"), 0.50)
    assert ok is False
    assert reason == "auc_nan"


# --------------------------------------------------------------- helpers locais
def _trending_candles(n: int = 300):
    closes = []
    price = 100.0
    for i in range(n):
        price += 0.5 if (i // 20) % 2 == 0 else -0.4
        closes.append(price)
    return make_candles(closes)


def _random_dataset(n: int = 300, n_features: int = 5, horizon: int = 3) -> Dataset:
    """Dataset aleatório: AUC ≈ 0.5, garantindo que o gate é testado no limite."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(n, n_features)).astype(np.float64)
    y = (rng.random(n) > 0.5).astype(np.int64)
    return Dataset(
        X=X,
        y=y,
        feature_names=[f"f{i}" for i in range(n_features)],
        times=[None] * n,  # type: ignore[list-item]
        indices=list(range(n)),
        horizon=horizon,
    )


# --------------------------------------------------------------- persistência
def test_training_marks_deployable_on_signal() -> None:
    candles = _trending_candles(300)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    result = train_classifier(ds, symbol="BTCUSDT", min_deploy_auc=0.50)
    # Dataset trending → AUC alto → deployable
    assert result.metadata.deployable is True
    assert result.metadata.deploy_reason is None


def test_training_marks_not_deployable_with_high_min() -> None:
    # Dataset aleatório (AUC ≈ 0.5) + min=0.6 → bloqueia determinístico.
    ds = _random_dataset()
    result = train_classifier(ds, symbol="BTCUSDT", min_deploy_auc=0.6)
    assert result.metadata.deployable is False
    assert result.metadata.deploy_reason is not None
    assert "auc_below_min" in result.metadata.deploy_reason


def test_metadata_roundtrip_via_load_metadata(tmp_path: Path) -> None:
    # Mesmo dataset aleatório para o bloqueio ser determinístico.
    ds = _random_dataset()
    result = train_classifier(ds, symbol="BTCUSDT", min_deploy_auc=0.6)
    path = save_training_result(result, tmp_path)
    loaded = ForwardReturnClassifier.load_metadata(path)
    assert loaded is not None
    assert loaded.deployable is False
    assert loaded.deploy_reason is not None


def test_metadata_missing_returns_none(tmp_path: Path) -> None:
    # Cria só o .joblib sem o .json ao lado
    ds = _random_dataset()
    result = train_classifier(ds, symbol="BTCUSDT", min_deploy_auc=0.6)
    path = save_training_result(result, tmp_path)
    path.with_suffix(".json").unlink()
    assert ForwardReturnClassifier.load_metadata(path) is None


# --------------------------------------------------------------- ml_strategy
def _write_model(
    tmp_path: Path,
    *,
    version: str,
    deployable: bool,
    reason: str | None,
) -> Path:
    """Grava um `.joblib` dummy + metadata para o loader testar."""
    path = tmp_path / f"{version}.joblib"
    candles = _trending_candles(80)
    ds = build_dataset(candles, pipeline=default_pipeline(), horizon=3)
    real = train_classifier(ds, symbol="X", min_deploy_auc=0.0)
    metadata = ModelMetadata(
        version=version,
        symbol="TESTUSDT",
        horizon=5,
        feature_names=real.metadata.feature_names,
        trained_at=real.metadata.trained_at,
        n_train=real.metadata.n_train,
        n_test=real.metadata.n_test,
        metrics=real.metadata.metrics,
        hyperparams=real.metadata.hyperparams,
        deployable=deployable,
        deploy_reason=reason,
    )
    real.model.save(path, metadata)
    return path


def test_ml_strategy_skips_non_deployable_and_uses_older(
    tmp_path: Path,
) -> None:
    # Antigo: deployable=True. Novo: deployable=False. Deve carregar o antigo.
    _write_model(
        tmp_path,
        version="TESTUSDT_h5_20260101T000000Z",
        deployable=True,
        reason=None,
    )
    _write_model(
        tmp_path,
        version="TESTUSDT_h5_20260102T000000Z",
        deployable=False,
        reason="auc_below_min (0.45 < 0.5)",
    )

    strategy = MLStrategy(symbol="TESTUSDT", model_dir=tmp_path, horizon=5)
    ctx = StrategyContext(
        symbol="TESTUSDT",
        market_type="FUTURES",  # type: ignore[arg-type]
        interval="5m",
        candles=_trending_candles(60),
    )
    strategy.generate(ctx)
    assert strategy._generator is not None  # type: ignore[attr-defined]


def test_ml_strategy_returns_none_when_all_non_deployable(
    tmp_path: Path,
) -> None:
    _write_model(
        tmp_path,
        version="TESTUSDT_h5_20260101T000000Z",
        deployable=False,
        reason="auc_below_min",
    )
    _write_model(
        tmp_path,
        version="TESTUSDT_h5_20260102T000000Z",
        deployable=False,
        reason="auc_below_min",
    )

    strategy = MLStrategy(symbol="TESTUSDT", model_dir=tmp_path, horizon=5)
    ctx = StrategyContext(
        symbol="TESTUSDT",
        market_type="FUTURES",  # type: ignore[arg-type]
        interval="5m",
        candles=_trending_candles(60),
    )
    result = strategy.generate(ctx)
    assert result is None
    assert strategy._generator is None  # type: ignore[attr-defined]


def test_ml_strategy_metadata_invalid_skipped(tmp_path: Path) -> None:
    """Modelo sem `.json` é ignorado (fail-safe: não deploya)."""
    bad = tmp_path / "TESTUSDT_h5_20260101T000000Z.joblib"
    bad.write_bytes(b"garbage")
    _write_model(
        tmp_path,
        version="TESTUSDT_h5_20260102T000000Z",
        deployable=True,
        reason=None,
    )

    strategy = MLStrategy(symbol="TESTUSDT", model_dir=tmp_path, horizon=5)
    ctx = StrategyContext(
        symbol="TESTUSDT",
        market_type="FUTURES",  # type: ignore[arg-type]
        interval="5m",
        candles=_trending_candles(60),
    )
    strategy.generate(ctx)
    assert strategy._generator is not None  # type: ignore[attr-defined]


# ------------------------------------------------- inferência de deployable
def test_load_metadata_infers_deployable_from_bad_auc(tmp_path: Path) -> None:
    """Metadados pré-ML-2b (sem campo `deployable`) com AUC ruim → False."""
    import json as _json

    model_path = tmp_path / "TESTUSDT_h5_20250101T000000Z.joblib"
    # Escreve um pipeline válido para o load não falhar (não chega a carregar)
    ds = _random_dataset()
    real = train_classifier(ds, symbol="TESTUSDT", min_deploy_auc=0.0)
    joblib.dump(real.model._pipeline, model_path)  # type: ignore[attr-defined]
    model_path.with_suffix(".json").write_text(
        _json.dumps({
            "version": "TESTUSDT_h5_20250101T000000Z",
            "symbol": "TESTUSDT",
            "horizon": 5,
            "feature_names": [],
            "trained_at": "2026-01-01T00:00:00Z",
            "n_train": 1,
            "n_test": 1,
            "metrics": {"auc": 0.42},
            "hyperparams": {},
            # sem "deployable"
        }),
        encoding="utf-8",
    )
    loaded = ForwardReturnClassifier.load_metadata(model_path)
    assert loaded is not None
    assert loaded.deployable is False
    assert loaded.deploy_reason is not None
    assert "inferred_from_auc" in loaded.deploy_reason


def test_load_metadata_keeps_good_old_model_deployable(tmp_path: Path) -> None:
    """Metadados pré-ML-2b com AUC bom continuam deployable."""
    import json as _json

    model_path = tmp_path / "TESTUSDT_h5_20250101T000000Z.joblib"
    ds = _random_dataset()
    real = train_classifier(ds, symbol="TESTUSDT", min_deploy_auc=0.0)
    joblib.dump(real.model._pipeline, model_path)  # type: ignore[attr-defined]
    model_path.with_suffix(".json").write_text(
        _json.dumps({
            "version": "TESTUSDT_h5_20250101T000000Z",
            "symbol": "TESTUSDT",
            "horizon": 5,
            "feature_names": [],
            "trained_at": "2026-01-01T00:00:00Z",
            "n_train": 1,
            "n_test": 1,
            "metrics": {"auc": 0.58},
            "hyperparams": {},
        }),
        encoding="utf-8",
    )
    loaded = ForwardReturnClassifier.load_metadata(model_path)
    assert loaded is not None
    assert loaded.deployable is True