"""Fase 6c: pré-registro e avaliação do modo sombra com regra de parada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.backtest import shadow_report
from app.backtest.lab import LabConfig
from app.backtest.registry import load_declared
from app.backtest.shadow import ShadowSpec, l6_lab_config, spec_from_registry
from app.backtest.shadow_report import (
    COLLECTING,
    FINAL,
    INTERIM,
    evaluate_shadow,
    header_markdown,
    stage_for,
)
from tests.unit.strategies.conftest import make_candles

T0 = datetime(2026, 10, 15, tzinfo=UTC)


# ------------------------------------------------------------- pré-registro
def test_l6_shadow_is_preregistered_before_its_start():
    (entry,) = [e for e in load_declared() if e.id == "L6-shadow"]
    assert entry.verdict == "EM TESTE" and entry.date == "2026-10-13"
    spec = spec_from_registry("L6-shadow", load_declared())
    assert spec is not None and spec.start == T0
    assert spec.lab == l6_lab_config()
    assert "100" in entry.notes and "300" in entry.notes and "12 meses" in entry.notes


def test_stopping_rule_constants_are_the_registered_ones():
    assert (shadow_report.INTERIM_TRADES, shadow_report.FINAL_TRADES, shadow_report.MAX_DAYS) == (100, 300, 365)


# -------------------------------------------------------------------- estágio
def _exits(n: int, every_h: float = 6) -> list[datetime]:
    return [T0 + timedelta(hours=every_h * (k + 1)) for k in range(n)]


def test_collecting_has_no_cutoff():
    s = stage_for(_exits(99), start=T0, now=T0 + timedelta(days=30))
    assert s.stage == COLLECTING and s.cutoff is None and s.closed_trades == 99


def test_interim_cuts_at_the_100th_exit_whatever_the_current_count():
    exits = _exits(250)
    s = stage_for(list(reversed(exits)), start=T0, now=T0 + timedelta(days=80))
    assert s.stage == INTERIM and s.cutoff == exits[99] and s.closed_trades == 250


def test_final_at_the_300th_exit():
    exits = _exits(340)
    s = stage_for(exits, start=T0, now=T0 + timedelta(days=100))
    assert s.stage == FINAL and s.cutoff == exits[299]


def test_final_at_twelve_months_without_enough_trades():
    deadline = T0 + timedelta(days=365)
    s = stage_for(_exits(120), start=T0, now=deadline)
    assert s.stage == FINAL and s.cutoff == deadline and "365 dias" in s.reason
    late = _exits(299) + [deadline + timedelta(hours=1)]  # o 300º só depois do prazo
    assert stage_for(late, start=T0, now=deadline + timedelta(days=1)).cutoff == deadline


# ------------------------------------------------------------------- avaliação
def _closes(n: int) -> list[float]:
    price, out = 100.0, []
    for k in range(n):
        price *= 1.003 if (k // 50) % 2 == 0 else 0.997
        out.append(price)
    return out


START = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * 100)
CANDLES = make_candles(_closes(1200))
SPEC = ShadowSpec(
    hypothesis="T", start=START, warmup_bars=100,
    lab=LabConfig(strategy="trend_breakout", symbols=["AAAUSDT", "BBBUSDT"], intervals=["5m"],
                  trailing_stop=True, funding=False, skip_vol=["HIGH"], random_replicates=20),
)


def _loader(candles):
    async def load(symbol, interval, days):
        return candles

    return load


async def _evaluate(candles):
    return await evaluate_shadow(SPEC, now=candles[-1].close_time + timedelta(seconds=1), loader=_loader(candles))


@pytest.fixture
def small_rule(monkeypatch):
    monkeypatch.setattr(shadow_report, "INTERIM_TRADES", 6)
    monkeypatch.setattr(shadow_report, "FINAL_TRADES", 400)


async def test_collecting_returns_no_statistics(monkeypatch):
    monkeypatch.setattr(shadow_report, "INTERIM_TRADES", 10_000)
    e = await _evaluate(CANDLES)
    assert e.stage.stage == COLLECTING and e.report is None and e.stage.closed_trades > 0
    assert "Ainda coletando" in header_markdown(e)


async def test_interim_uses_only_trades_up_to_the_cutoff(small_rule):
    e = await _evaluate(CANDLES)
    assert e.stage.stage == INTERIM and e.report is not None
    report = e.report
    assert report.overall.trades >= 6 and report.period_start == START and report.period_end == e.stage.cutoff
    assert report.config.tested_hypotheses == 1                 # confirmatório: sem Bonferroni
    assert "só informativa, não é veredito" in header_markdown(e)


async def test_evaluation_does_not_depend_on_when_it_runs(small_rule):
    early = await _evaluate(CANDLES[:900])
    late = await _evaluate(CANDLES)
    assert early.stage.stage == late.stage.stage == INTERIM
    assert early.stage.cutoff == late.stage.cutoff
    assert early.stage.closed_trades < late.stage.closed_trades
    assert early.report is not None and late.report is not None
    assert early.report.overall == late.report.overall
    assert early.report.ci95_net_pct == late.report.ci95_net_pct


async def test_final_by_deadline_is_the_official_verdict(monkeypatch):
    monkeypatch.setattr(shadow_report, "FINAL_TRADES", 10_000)
    monkeypatch.setattr(shadow_report, "MAX_DAYS", 2)
    e = await _evaluate(CANDLES)
    assert e.stage.stage == FINAL and e.stage.cutoff == START + timedelta(days=2)
    assert e.report is not None and not e.report.verdict.approved  # poucos trades: amostra falha
    assert "Avaliação final pré-registrada" in header_markdown(e)
