"""Fase 4b: registro de hipóteses (correção para múltiplos testes) e
sensibilidade a parâmetros."""

from __future__ import annotations

import json

import pytest

from app.backtest.lab import LabConfig, run_lab
from app.backtest.registry import (
    DeclaredHypothesis,
    append_declared,
    bonferroni,
    load_declared,
    record_run,
)
from app.backtest.sweep import grid_points, run_sweep, to_markdown
from scripts.strategy_lab import parse_cli
from tests.unit.strategies.conftest import make_candles


def _hyp(hid: str) -> DeclaredHypothesis:
    return DeclaredHypothesis(id=hid, date="2026-10-12", strategy="momentum",
                              universe="BTC | 1h | 30 dias", verdict="REPROVADA")


def test_declared_registry_roundtrip_and_rejects_duplicates(tmp_path):
    path = tmp_path / "hypotheses.json"
    assert load_declared(path) == []
    assert append_declared(_hyp("A"), path) == 1
    assert append_declared(_hyp("B"), path) == 2
    assert [h.id for h in load_declared(path)] == ["A", "B"]
    with pytest.raises(ValueError):
        append_declared(_hyp("A"), path)


def test_repository_registry_has_the_past_declared_verdicts():
    ids = [h.id for h in load_declared()]
    assert ids[:4] == ["L2-momentum", "L3b-trend-breakout", "L4b-xs-momentum", "L5a1-mean-reversion"]


def test_bonferroni():
    assert bonferroni(0.015, 1) == pytest.approx(0.015)
    assert bonferroni(0.015, 5) == pytest.approx(0.075)
    assert bonferroni(0.4, 5) == 1.0


def test_record_run_appends_with_git_commit(tmp_path):
    path = tmp_path / "runs.jsonl"
    record_run("lab", {"strategy": "x"}, path)
    record_run("sweep", {"strategy": "y"}, path)
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [x["kind"] for x in lines] == ["lab", "sweep"]
    assert all(x["git_commit"] for x in lines)


# -------------------------------------------------------------- dados falsos
def _wave(n: int = 900) -> list:
    closes = [100 * (1.003 ** k) if (k // 50) % 2 == 0 else 100 * (1.003 ** 50) * (0.997 ** (k % 50))
              for k in range(n)]
    return make_candles(closes)


async def test_timing_gate_is_corrected_by_the_number_of_hypotheses():
    candles = _wave()

    async def loader(symbol, interval, days):
        return candles

    base = LabConfig(strategy="trend_breakout", symbols=["AAA"], intervals=["1h"],
                     trailing_stop=True, max_position_notional=None, random_replicates=50)
    one = await run_lab(base, loader=loader)
    many = await run_lab(base.model_copy(update={"tested_hypotheses": 5}), loader=loader)
    assert many.timing.p_value == one.timing.p_value
    assert many.timing_p_adjusted == pytest.approx(min(1.0, one.timing.p_value * 5))
    check = next(c for c in many.verdict.checks if c.name == "timing_vs_aleatorio")
    assert "× 5 hipóteses" in check.value


def test_grid_points_cartesian_product():
    pts = grid_points({"b": [1.0, 2.0], "a": [10.0, 20.0, 30.0]})
    assert len(pts) == 6 and {"a": 10.0, "b": 1.0} in pts


async def test_sweep_runs_every_point_loads_data_once_and_marks_declared():
    candles = _wave()
    loads: list[tuple] = []

    async def loader(symbol, interval, days):
        loads.append((symbol, interval))
        return candles

    config = LabConfig(strategy="trend_breakout", symbols=["AAA"], intervals=["1h"],
                       trailing_stop=True, max_position_notional=None, random_replicates=20)
    report = await run_sweep(config, {"channel_period": [10.0, 20.0], "stop_atr_mult": [2.0, 3.0]},
                             loader=loader)
    assert len(report.points) == 4
    assert loads == [("AAA", "1h")]  # carregado uma vez para a grade inteira
    declared = [p for p in report.points if p.declared]
    assert [p.params for p in declared] == [{"channel_period": 20.0, "stop_atr_mult": 3.0}]  # padrões
    md = to_markdown(report)
    assert "★ channel_period=20, stop_atr_mult=3" in md and "viés de seleção" in md


def test_cli_sweep_and_declared_options():
    cfg, grid, declared = parse_cli(["--strategy", "trend_breakout", "--sweep", "channel_period=15,20,30"])
    assert grid == {"channel_period": [15.0, 20.0, 30.0]} and declared is None
    assert isinstance(cfg, LabConfig) and cfg.tested_hypotheses == len(load_declared()) + 1
    cfg, grid, declared = parse_cli(["--strategy", "trend_breakout", "--declared", "L-novo"])
    assert grid == {} and declared == "L-novo"
    with pytest.raises(SystemExit):
        parse_cli(["--strategy", "trend_breakout", "--sweep", "channel_period=15", "--declared", "X"])
    with pytest.raises(SystemExit):
        parse_cli(["--strategy", "trend_breakout", "--sweep", "channel_period"])


def test_declared_registry_is_written_with_lf(tmp_path):
    path = tmp_path / "hypotheses.json"
    append_declared(_hyp("A"), path)
    assert b"\r\n" not in path.read_bytes()
