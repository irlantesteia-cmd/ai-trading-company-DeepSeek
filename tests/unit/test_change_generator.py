"""Testes para `HeuristicChangeGenerator`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.change_generator import HeuristicChangeGenerator

_REPO_PATH = "config/evolution/thresholds.json"


def _write_config(tmp_path: Path, long_t: float = 0.55, short_t: float = 0.45) -> Path:
    path = tmp_path / "thresholds.json"
    path.write_text(
        json.dumps({"long_threshold": long_t, "short_threshold": short_t}),
        encoding="utf-8",
    )
    return path


def _make_generator(path: Path, **overrides) -> HeuristicChangeGenerator:
    return HeuristicChangeGenerator(config_path=path, repo_path=_REPO_PATH, **overrides)


def _healthy_metrics(**overrides: float) -> dict[str, float]:
    base = {
        "num_trades": 50.0,
        "total_pnl": 100.0,
        "total_fees": 5.0,
        "win_rate": 0.55,
        "avg_win": 10.0,
        "avg_loss": -5.0,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_no_change_when_healthy(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    gen = _make_generator(path)
    assert await gen(_healthy_metrics()) is None


@pytest.mark.asyncio
async def test_no_change_when_not_enough_trades(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    gen = _make_generator(path)
    metrics = _healthy_metrics(num_trades=5.0, win_rate=0.10)
    assert await gen(metrics) is None


@pytest.mark.asyncio
async def test_change_on_low_win_rate(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    gen = _make_generator(path)
    metrics = _healthy_metrics(win_rate=0.30)
    change = await gen(metrics)
    assert change is not None
    assert "tighten ML thresholds" in change.title
    payload = json.loads(change.files[0].content)
    assert payload["long_threshold"] == pytest.approx(0.60)
    assert payload["short_threshold"] == pytest.approx(0.40)
    assert change.files[0].path == _REPO_PATH


@pytest.mark.asyncio
async def test_change_on_asymmetry(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    gen = _make_generator(path)
    metrics = _healthy_metrics(avg_win=5.0, avg_loss=-15.0)  # ratio 3.0 > 2.0
    change = await gen(metrics)
    assert change is not None


@pytest.mark.asyncio
async def test_change_on_negative_pnl(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    gen = _make_generator(path)
    metrics = _healthy_metrics(total_pnl=-100.0)
    change = await gen(metrics)
    assert change is not None


@pytest.mark.asyncio
async def test_at_limit_returns_none(tmp_path: Path) -> None:
    path = _write_config(tmp_path, long_t=0.70, short_t=0.30)
    gen = _make_generator(path)
    metrics = _healthy_metrics(win_rate=0.10)
    assert await gen(metrics) is None


@pytest.mark.asyncio
async def test_missing_config_returns_none(tmp_path: Path) -> None:
    gen = HeuristicChangeGenerator(
        config_path=tmp_path / "nope.json", repo_path=_REPO_PATH
    )
    metrics = _healthy_metrics(win_rate=0.10)
    assert await gen(metrics) is None