from datetime import UTC, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.core.enums import MarketType
from app.features.pipeline import default_pipeline
from app.strategies.context import StrategyContext
from app.strategies.ml_strategy import MLStrategy
from tests.unit.strategies.conftest import make_candles


def _fake_pipeline(n_features: int | None = None) -> Pipeline:
    """Pipeline `sklearn` fit num dataset sintético.

    `n_features` default é o tamanho do pipeline atual — assim o modelo
    fake é compatível com o `MLSignalGenerator` no momento da predição.
    """
    n = n_features if n_features is not None else len(default_pipeline().names)
    X = np.random.default_rng(seed=42).normal(size=(80, n))
    y = (X[:, 0] > 0).astype(int)
    return Pipeline(
        [("scaler", StandardScaler()), ("clf", LogisticRegression(max_iter=200))]
    ).fit(X, y)


def _metadata_payload(
    *,
    version: str,
    symbol: str = "BTCUSDT",
    horizon: int = 5,
    deployable: bool = True,
    deploy_reason: str | None = None,
    auc: float = 0.99,
    trained_at: str | None = None,
    feature_names: list[str] | None = None,
) -> str:
    """Metadata JSON do modelo fake.

    `auc=0.99` por padrão para passar o Gate 4. `trained_at` default é
    "agora" para passar o Gate 2 (boot floor). `feature_names` default é o
    pipeline atual para passar o Gate 3 (feature compat).
    """
    import json

    if trained_at is None:
        trained_at = datetime.now(UTC).isoformat()
    if feature_names is None:
        feature_names = default_pipeline().names

    return json.dumps(
        {
            "version": version,
            "symbol": symbol,
            "horizon": horizon,
            "feature_names": feature_names,
            "trained_at": trained_at,
            "n_train": 1,
            "n_test": 1,
            "metrics": {"auc": auc},
            "hyperparams": {},
            "deployable": deployable,
            "deploy_reason": deploy_reason,
        }
    )


def _write_model(
    tmp_path: Path,
    *,
    version: str,
    deployable: bool = True,
    deploy_reason: str | None = None,
    auc: float = 0.99,
    trained_at: str | None = None,
    feature_names: list[str] | None = None,
    pipeline: Pipeline | None = None,
) -> Path:
    """Grava `.joblib` + metadata `.json` ao lado (necessário para os gates)."""
    model_path = tmp_path / f"{version}.joblib"
    joblib.dump(pipeline or _fake_pipeline(), model_path)
    model_path.with_suffix(".json").write_text(
        _metadata_payload(
            version=version,
            deployable=deployable,
            deploy_reason=deploy_reason,
            auc=auc,
            trained_at=trained_at,
            feature_names=feature_names,
        ),
        encoding="utf-8",
    )
    return model_path


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
    _write_model(tmp_path, version="BTCUSDT_h5_20250101T000000Z")
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)

    sig = strat.generate(_ctx())
    assert sig is None or sig.symbol == "BTCUSDT"


def test_caches_model_between_calls(tmp_path: Path):
    _write_model(tmp_path, version="BTCUSDT_h5_20250101T000000Z")
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)

    strat.generate(_ctx())
    assert strat._generator is not None

    _write_model(tmp_path, version="BTCUSDT_h5_20250102T000000Z")
    strat.generate(_ctx())
    assert strat._generator is not None


def test_warmup_value():
    strat = MLStrategy(symbol="BTCUSDT", model_dir=Path("/nonexistent"))
    assert strat.warmup == 30


def test_corrupted_model_is_swallowed(tmp_path: Path):
    model_path = tmp_path / "BTCUSDT_h5_20250101T000000Z.joblib"
    model_path.write_bytes(b"not a joblib file")
    model_path.with_suffix(".json").write_text(
        _metadata_payload(version="BTCUSDT_h5_20250101T000000Z"),
        encoding="utf-8",
    )
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    assert strat.generate(_ctx()) is None


def test_skips_non_deployable_model(tmp_path: Path):
    """Gate 1: modelo com `deployable=False` não é carregado."""
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=False,
        deploy_reason="auc_below_min (0.45 < 0.5)",
    )
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


def test_uses_older_deployable_when_newest_is_blocked(tmp_path: Path):
    """Se o mais recente está bloqueado, o loader cai para o anterior bom."""
    _write_model(tmp_path, version="BTCUSDT_h5_20250101T000000Z")
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250102T000000Z",
        deployable=False,
        deploy_reason="auc_below_min",
    )
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    strat.generate(_ctx())
    assert strat._generator is not None
    assert strat._loaded_path is not None
    assert "20250101" in strat._loaded_path.name


def test_metadata_missing_blocks_load(tmp_path: Path):
    """`.joblib` sem `.json` ao lado é fail-safe: não deploya."""
    model_path = tmp_path / "BTCUSDT_h5_20250101T000000Z.joblib"
    joblib.dump(_fake_pipeline(), model_path)
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


def test_reloads_when_newer_deployable_appears(tmp_path: Path):
    """Se aparece modelo novo deployável, o loader troca automaticamente."""
    _write_model(tmp_path, version="BTCUSDT_h5_20250101T000000Z")
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    strat.generate(_ctx())
    assert strat._generator is not None
    first_path = strat._loaded_path
    assert first_path is not None

    _write_model(tmp_path, version="BTCUSDT_h5_20250102T000000Z")
    strat.generate(_ctx())
    assert strat._loaded_path is not None
    assert strat._loaded_path != first_path
    assert "20250102" in strat._loaded_path.name


def test_drops_generator_when_all_remaining_are_non_deployable(tmp_path: Path):
    """Se o único deployável vira non-deployable, o gerador é derrubado."""
    _write_model(tmp_path, version="BTCUSDT_h5_20250101T000000Z")
    strat = MLStrategy(symbol="BTCUSDT", model_dir=tmp_path)
    strat.generate(_ctx())
    assert strat._generator is not None

    (tmp_path / "BTCUSDT_h5_20250101T000000Z.json").write_text(
        _metadata_payload(
            version="BTCUSDT_h5_20250101T000000Z",
            deployable=False,
            deploy_reason="auc_below_min",
        ),
        encoding="utf-8",
    )
    strat.generate(_ctx())
    assert strat._generator is None


# ------------------------------------------------ gate de AUC (ML-2b)
def test_blocks_metadata_deployable_but_auc_below_current_threshold(
    tmp_path: Path,
):
    """Gate 4: metadata diz deployable=True, mas AUC < min corrente."""
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.52,
    )
    strat = MLStrategy(
        symbol="BTCUSDT", model_dir=tmp_path, min_deploy_auc=0.55
    )
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


def test_blocks_inferred_deployable_when_auc_below_threshold(
    tmp_path: Path,
):
    """Gate 4 cobre modelos pré-ML-2b (sem campo `deployable`)."""
    import json as _json

    model_path = tmp_path / "BTCUSDT_h5_20250101T000000Z.joblib"
    joblib.dump(_fake_pipeline(), model_path)
    model_path.with_suffix(".json").write_text(
        _json.dumps({
            "version": "BTCUSDT_h5_20250101T000000Z",
            "symbol": "BTCUSDT",
            "horizon": 5,
            "feature_names": default_pipeline().names,
            "trained_at": datetime.now(UTC).isoformat(),
            "n_train": 1,
            "n_test": 1,
            "metrics": {"auc": 0.51},
            "hyperparams": {},
        }),
        encoding="utf-8",
    )
    strat = MLStrategy(
        symbol="BTCUSDT", model_dir=tmp_path, min_deploy_auc=0.55
    )
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


def test_passes_with_auc_above_current_threshold(tmp_path: Path):
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.58,
    )
    strat = MLStrategy(
        symbol="BTCUSDT", model_dir=tmp_path, min_deploy_auc=0.55
    )
    strat.generate(_ctx())
    assert strat._generator is not None


# ------------------------------------------------ gate boot floor (ML-3-K)
def test_skips_pre_session_model(tmp_path: Path):
    """Gate 2: modelo de sessão anterior é rejeitado mesmo se deployable."""
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.99,
        trained_at=old,
    )
    strat = MLStrategy(
        symbol="BTCUSDT",
        model_dir=tmp_path,
        min_deploy_auc=0.55,
        boot_grace_seconds=120.0,
    )
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


def test_accepts_model_within_grace_window(tmp_path: Path):
    """Gate 2: modelo recente (dentro do grace) passa."""
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.99,
        trained_at=recent,
    )
    strat = MLStrategy(
        symbol="BTCUSDT",
        model_dir=tmp_path,
        min_deploy_auc=0.55,
        boot_grace_seconds=120.0,
    )
    strat.generate(_ctx())
    assert strat._generator is not None


def test_rejects_invalid_trained_at(tmp_path: Path):
    """Gate 2: `trained_at` inválido → fail-closed."""
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.99,
        trained_at="not-a-date",
    )
    strat = MLStrategy(
        symbol="BTCUSDT",
        model_dir=tmp_path,
        min_deploy_auc=0.55,
        boot_grace_seconds=120.0,
    )
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


# ------------------------------------------------ gate features (ML-3a)
def test_rejects_model_with_different_feature_set(tmp_path: Path):
    """Gate 3: modelo com features antigas (13) é rejeitado no pipeline novo (8)."""
    old_features = [
        "return_1", "return_3", "return_5", "return_10", "log_return_1",
        "volatility_20", "rsi_14", "volume_zscore_20",
        "ema_fast_dist_9", "ema_slow_dist_21", "atr_norm_14",
        "high_low_range", "body_ratio",
    ]
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.99,
        feature_names=old_features,
    )
    strat = MLStrategy(
        symbol="BTCUSDT",
        model_dir=tmp_path,
        min_deploy_auc=0.55,
    )
    assert strat.generate(_ctx()) is None
    assert strat._generator is None


def test_accepts_model_with_matching_feature_set(tmp_path: Path):
    """Gate 3: modelo com features idênticas ao pipeline é aceito."""
    _write_model(
        tmp_path,
        version="BTCUSDT_h5_20250101T000000Z",
        deployable=True,
        auc=0.99,
        feature_names=default_pipeline().names,
    )
    strat = MLStrategy(
        symbol="BTCUSDT",
        model_dir=tmp_path,
        min_deploy_auc=0.55,
    )
    strat.generate(_ctx())
    assert strat._generator is not None