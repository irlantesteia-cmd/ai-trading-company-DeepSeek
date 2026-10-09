import json
from pathlib import Path

from app.ml.model import ModelMetadata
from app.ml.retention import prune_models


def _write(model_dir: Path, version: str, *, deployable: bool = False) -> Path:
    symbol, h, _ = version.split("_")
    path = model_dir / f"{version}.joblib"
    path.write_bytes(b"model")
    meta = ModelMetadata(
        version=version,
        symbol=symbol,
        horizon=int(h[1:]),
        feature_names=["return_1"],
        trained_at="2026-10-09T00:00:00+00:00",
        n_train=1,
        n_test=1,
        deployable=deployable,
    )
    path.with_suffix(".json").write_text(json.dumps(meta.to_dict()), encoding="utf-8")
    return path


def _stems(model_dir: Path) -> list[str]:
    return sorted(p.name for p in model_dir.iterdir() if p.is_file())


def test_keeps_newest_n_and_removes_pairs(tmp_path):
    for day in range(1, 8):
        _write(tmp_path, f"BTCUSDT_h5_2026100{day}T120000Z")

    removed = prune_models(tmp_path, symbol="BTCUSDT", horizon=5, keep=3)

    assert [p.stem for p in removed] == [
        "BTCUSDT_h5_20261004T120000Z",
        "BTCUSDT_h5_20261003T120000Z",
        "BTCUSDT_h5_20261002T120000Z",
        "BTCUSDT_h5_20261001T120000Z",
    ]
    assert _stems(tmp_path) == [
        f"BTCUSDT_h5_2026100{d}T120000Z.{ext}" for d in (5, 6, 7) for ext in ("joblib", "json")
    ]


def test_newest_deployable_is_kept_even_if_old(tmp_path):
    _write(tmp_path, "BTCUSDT_h5_20261001T120000Z", deployable=True)  # mais novo deployable
    _write(tmp_path, "BTCUSDT_h5_20260930T120000Z", deployable=True)  # deployable mais antigo
    for day in range(2, 6):
        _write(tmp_path, f"BTCUSDT_h5_2026100{day}T120000Z")

    prune_models(tmp_path, symbol="BTCUSDT", horizon=5, keep=2)

    kept = {p.stem for p in tmp_path.glob("*.joblib")}
    assert kept == {
        "BTCUSDT_h5_20261005T120000Z",
        "BTCUSDT_h5_20261004T120000Z",
        "BTCUSDT_h5_20261001T120000Z",
    }


def test_other_symbols_horizons_names_and_subdirs_untouched(tmp_path):
    for day in range(1, 4):
        _write(tmp_path, f"BTCUSDT_h5_2026100{day}T120000Z")
    _write(tmp_path, "BTCUSDT_h15_20261001T120000Z")
    _write(tmp_path, "ETHUSDT_h5_20261001T120000Z")
    (tmp_path / "BTCUSDT_h5_manual.joblib").write_bytes(b"x")
    (tmp_path / "backup").mkdir()
    _write(tmp_path / "backup", "BTCUSDT_h5_20260901T120000Z")

    removed = prune_models(tmp_path, symbol="BTCUSDT", horizon=5, keep=1)

    assert len(removed) == 2
    assert (tmp_path / "BTCUSDT_h15_20261001T120000Z.joblib").exists()
    assert (tmp_path / "ETHUSDT_h5_20261001T120000Z.joblib").exists()
    assert (tmp_path / "BTCUSDT_h5_manual.joblib").exists()
    assert (tmp_path / "backup" / "BTCUSDT_h5_20260901T120000Z.joblib").exists()


def test_keep_zero_disables_and_missing_dir_is_noop(tmp_path):
    for day in range(1, 4):
        _write(tmp_path, f"BTCUSDT_h5_2026100{day}T120000Z")

    assert prune_models(tmp_path, symbol="BTCUSDT", horizon=5, keep=0) == []
    assert len(list(tmp_path.glob("*.joblib"))) == 3
    assert prune_models(tmp_path / "nope", symbol="BTCUSDT", horizon=5, keep=1) == []
