"""Fase 6b: serviço do modo sombra (registro, ciclo, gravação e agenda)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.backtest.lab import LabConfig
from app.backtest.registry import DeclaredHypothesis, append_declared, load_declared
from app.backtest.shadow import spec_from_registry
from app.backtest.shadow_runner import (
    INVALID,
    NOT_REGISTERED,
    REGISTERED,
    DbShadowStore,
    ShadowCycle,
    ShadowRunner,
)
from app.database.models.shadow_run import ShadowRunORM
from scripts.run_shadow import seconds_until_next_cycle
from tests.unit.strategies.conftest import make_candles


def _entry(**kw) -> DeclaredHypothesis:
    base = {"id": "L6-shadow", "date": "2026-10-14", "strategy": "trend_breakout",
            "universe": "12 ativos | 4h, 1d | para a frente", "verdict": "EM TESTE",
            "shadow_start": "2026-10-15T00:00:00+00:00"}
    return DeclaredHypothesis(**{**base, **kw})


# ------------------------------------------------------------------ registro
def test_spec_comes_from_the_registered_start():
    spec = spec_from_registry("L6-shadow", [_entry()])
    assert spec is not None and spec.start == datetime(2026, 10, 15, tzinfo=UTC)
    assert spec.lab.skip_vol == ["HIGH"] and spec.hypothesis == "L6-shadow"
    naive = spec_from_registry("L6-shadow", [_entry(shadow_start="2026-10-15T00:00:00")])
    assert naive is not None and naive.start.tzinfo is not None


def test_no_spec_without_registration_or_start():
    assert spec_from_registry("L6-shadow", []) is None
    assert spec_from_registry("L6-shadow", [_entry(shadow_start=None)]) is None


def test_start_before_registration_or_other_strategy_is_refused():
    with pytest.raises(ValueError, match="antes do registro"):
        spec_from_registry("L6-shadow", [_entry(shadow_start="2026-10-01T00:00:00+00:00")])
    with pytest.raises(ValueError, match="diferentes"):
        spec_from_registry("L6-shadow", [_entry(strategy="momentum")])
    with pytest.raises(ValueError, match="diferentes"):
        spec_from_registry("L6-shadow", [_entry(params={"channel_period": 30})])


def test_lab_entries_are_written_without_the_shadow_field(tmp_path):
    path = tmp_path / "h.json"
    append_declared(_entry(id="L9-lab", shadow_start=None), path)
    append_declared(_entry(), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "shadow_start" not in raw[0] and raw[1]["shadow_start"] == "2026-10-15T00:00:00+00:00"
    assert load_declared(path)[1].shadow_start == "2026-10-15T00:00:00+00:00"


# --------------------------------------------------------------------- ciclo
class _Store:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.cycles: list[ShadowCycle] = []

    async def save_trades(self, rows):
        self.rows.extend(rows)

    async def record_cycle(self, cycle):
        self.cycles.append(cycle)


def _closes(n: int) -> list[float]:
    price, out = 100.0, []
    for k in range(n):
        price *= 1.003 if (k // 50) % 2 == 0 else 0.997
        out.append(price)
    return out


START = datetime(2026, 10, 15, tzinfo=UTC)
CANDLES = make_candles(_closes(400), start=START - timedelta(minutes=5 * 100))
NOW = CANDLES[-1].close_time + timedelta(seconds=30)


@pytest.fixture
def small_universe(monkeypatch):
    monkeypatch.setattr(
        "app.backtest.shadow.l6_lab_config",
        lambda: LabConfig(strategy="trend_breakout", symbols=["AAAUSDT", "BBBUSDT"], intervals=["4h", "1d"],
                          trailing_stop=True, funding=True, skip_vol=["HIGH"]),
    )


def _registry(tmp_path, *entries) -> object:
    path = tmp_path / "hypotheses.json"
    for e in entries:
        append_declared(e, path)
    return path


async def test_not_registered_cycle_is_recorded_and_runs_nothing(tmp_path):
    store = _Store()
    calls: list = []

    async def loader(*a):
        calls.append(a)
        return CANDLES

    runner = ShadowRunner(hypothesis="L6-shadow", registry_path=tmp_path / "missing.json", store=store,
                          loader=loader)
    cycle = await runner.run_once(NOW)
    assert cycle.status == NOT_REGISTERED and store.cycles == [cycle] and calls == [] and store.rows == []


async def test_invalid_registry_is_recorded_with_the_error(tmp_path):
    store = _Store()
    path = _registry(tmp_path, _entry(shadow_start="2026-01-01T00:00:00+00:00"))
    cycle = await ShadowRunner(hypothesis="L6-shadow", registry_path=path, store=store).run_once(NOW)
    assert cycle.status == INVALID and "antes do registro" in cycle.error


async def test_registered_cycle_runs_every_stream_and_loads_funding_once(tmp_path, small_universe):
    store = _Store()
    funding_calls: list[str] = []

    async def loader(symbol, interval, days):
        return CANDLES

    async def funding_loader(symbol, days):
        funding_calls.append(symbol)
        return []

    path = _registry(tmp_path, _entry())
    runner = ShadowRunner(hypothesis="L6-shadow", registry_path=path, store=store, loader=loader,
                          funding_loader=funding_loader)
    cycle = await runner.run_once(NOW)
    assert cycle.status == REGISTERED and cycle.start == START and cycle.streams == 4
    assert cycle.streams_failed == 0 and cycle.error is None
    assert funding_calls == ["AAAUSDT", "BBBUSDT"]                  # uma vez por ativo
    assert store.rows and all(r["entry_time"] >= START for r in store.rows)
    assert cycle.closed_trades + cycle.open_positions == len(store.rows)
    assert cycle.streams_short_warmup == 4                           # 100 barras < 600
    assert cycle.last_candle_close == CANDLES[-1].close_time
    assert cycle.streams_stale == 0


async def test_one_failing_stream_does_not_stop_the_others(tmp_path, small_universe):
    store = _Store()

    async def loader(symbol, interval, days):
        if symbol == "BBBUSDT" and interval == "4h":
            raise RuntimeError("binance fora")
        return CANDLES

    async def funding_loader(symbol, days):
        return []

    path = _registry(tmp_path, _entry())
    cycle = await ShadowRunner(hypothesis="L6-shadow", registry_path=path, store=store, loader=loader,
                               funding_loader=funding_loader).run_once(NOW)
    assert cycle.streams == 4 and cycle.streams_failed == 1
    assert "BBBUSDT 4h: binance fora" in cycle.error
    assert {r["symbol"] for r in store.rows} == {"AAAUSDT", "BBBUSDT"}


async def test_old_candles_mark_the_stream_as_stale(tmp_path, small_universe):
    store = _Store()

    async def loader(symbol, interval, days):
        return CANDLES

    async def funding_loader(symbol, days):
        return []

    path = _registry(tmp_path, _entry())
    later = NOW + timedelta(hours=9)  # > 2 velas de 4h sem vela nova; 1d ainda não
    cycle = await ShadowRunner(hypothesis="L6-shadow", registry_path=path, store=store, loader=loader,
                               funding_loader=funding_loader).run_once(later)
    assert cycle.streams_stale == 2


# ------------------------------------------------------------------- gravação
class _Session:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.sink.append(obj)

    async def commit(self):
        self.sink.append("commit")


async def test_db_store_records_the_cycle_and_skips_empty_trades():
    sink: list = []
    store = DbShadowStore(lambda: _Session(sink))  # type: ignore[arg-type]
    await store.save_trades([])
    assert sink == []
    await store.record_cycle(ShadowCycle(hypothesis="L6-shadow", ran_at=NOW, status=NOT_REGISTERED))
    orm, done = sink
    assert isinstance(orm, ShadowRunORM) and orm.status == NOT_REGISTERED and done == "commit"


# ---------------------------------------------------------------------- agenda
def test_next_cycle_is_aligned_to_the_4h_close_plus_offset():
    four_h = 14_400.0
    boundary = datetime(2026, 10, 15, 8, tzinfo=UTC).timestamp()
    assert seconds_until_next_cycle(four_h, 90, now=boundary) == 90
    assert seconds_until_next_cycle(four_h, 90, now=boundary + 90) == four_h
    assert seconds_until_next_cycle(four_h, 90, now=boundary + 100) == four_h - 10


# ------------------------------------------------------------------ provisões
def _compose_service(name: str) -> str:
    text = Path("docker-compose.yml").read_text(encoding="utf-8")
    block = text.split(f"\n  {name}:\n", 1)[1]
    return block.split("\n\n", 1)[0]  # até a próxima linha em branco (fim do serviço)


def test_compose_shadow_service_has_no_env_file_and_reads_the_registry():
    service = _compose_service("shadow")
    assert "env_file" not in service and "profiles" not in service
    assert "- ./docs/research:/app/docs/research:ro" in service
    assert 'command: ["python", "scripts/run_shadow.py"]' in service


def test_grafana_has_the_shadow_row_and_the_alert():
    with open("docker/grafana/dashboards/trading-overview.json", encoding="utf-8") as f:
        panels = json.load(f)["panels"]
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids))
    assert any(p["type"] == "row" and p["title"].startswith("Modo sombra") for p in panels)
    rules = Path("docker/grafana/provisioning/alerting/rules.yml").read_text(encoding="utf-8")
    rule = rules.split("- uid: shadow-silent", 1)[1]
    assert "FROM shadow_runs" in rule and "HAVING COUNT(*) > 0" in rule and "noDataState: OK" in rule
