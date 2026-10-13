"""Fase 6a: motor do modo sombra (teste para a frente sem ordens)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from app.backtest.engine import BacktestEngine
from app.backtest.lab import build_strategy, engine_config
from app.backtest.regime import classify_regimes
from app.backtest.shadow import (
    CLOSED,
    L6_SYMBOLS,
    OPEN,
    ShadowSpec,
    l6_lab_config,
    run_shadow,
    shadow_rows,
)
from app.backtest.types import BacktestConfig
from app.core.enums import MarketType
from app.database.repositories.shadow_trade import ShadowTradeRepository
from tests.unit.backtest.test_engine import _OneShotStrategy
from tests.unit.strategies.conftest import make_candles


def _closes(n: int) -> list[float]:
    """Subidas e quedas de 50 barras (rompimentos nos dois sentidos)."""
    price, out = 100.0, []
    for k in range(n):
        price *= 1.003 if (k // 50) % 2 == 0 else 0.997
        out.append(price)
    return out


CANDLES = make_candles(_closes(900))


def _spec(start_index: int, warmup_bars: int = 100) -> ShadowSpec:
    return ShadowSpec(hypothesis="T", start=CANDLES[start_index].open_time, warmup_bars=warmup_bars)


def _comparable(trades):
    return [t.model_dump(exclude={"signal_id"}) for t in trades]


def _run(spec, candles):
    return run_shadow(spec, symbol="BTCUSDT", interval="5m", candles=candles)


# ------------------------------------------------------------------- motor
def _cfg(**kw) -> BacktestConfig:
    return BacktestConfig(initial_capital=Decimal(10000), risk_per_trade_pct=0.01,
                          fee_bps=0.0, slippage_bps=0.0, **kw)


def test_engine_ignores_entries_before_first_entry_time():
    candles = make_candles([100.0] * 10 + [100.0, 100.0, 100.0, 105.0, 105.0])
    strategy = _OneShotStrategy(trigger_at=10)  # sinal na barra 9 → entrada na 10

    def run(first_entry):
        return BacktestEngine(_cfg(first_entry_time=first_entry)).run(
            symbol="BTCUSDT", interval="5m", market_type=MarketType.FUTURES,
            candles=candles, strategy=strategy)

    assert run(candles[11].open_time).trades == []
    (trade,) = run(candles[10].open_time).trades
    assert trade.entry_time == candles[10].open_time


# ------------------------------------------------------------------- sombra
def test_no_trade_enters_before_the_start():
    spec = _spec(400)
    run = _run(spec, CANDLES)
    assert run.trades and all(t.entry_time >= spec.start for t in run.trades)
    assert run.warmup_candles == 100 and run.candles == 600


def test_same_rules_as_the_lab_when_starting_at_the_first_candle():
    spec = _spec(0, warmup_bars=0)
    lab = BacktestEngine(engine_config(spec.lab)).run(
        symbol="BTCUSDT", interval="5m", market_type=MarketType.FUTURES, candles=CANDLES,
        strategy=build_strategy("trend_breakout", {}), regimes=classify_regimes(CANDLES))
    assert lab.trades
    assert _comparable(_run(spec, CANDLES).trades) == _comparable(lab.trades)


def test_closed_trades_do_not_change_when_new_candles_arrive():
    spec = _spec(300)
    before = [t for t in _run(spec, CANDLES[:700]).trades if t.exit_reason != "end"]
    after = _run(spec, CANDLES).trades
    assert before and _comparable(after[: len(before)]) == _comparable(before)


def test_restart_and_extra_history_give_the_same_result():
    spec = _spec(400)
    exact = _run(spec, CANDLES[300:])        # começa no início fixo do histórico
    longer = _run(spec, CANDLES)              # carregou mais do que precisava
    again = _run(spec, CANDLES)               # "reinício"
    assert _comparable(exact.trades) == _comparable(longer.trades) == _comparable(again.trades)


def test_open_position_is_marked_at_the_last_close():
    candles = make_candles(_closes(600) + [_closes(600)[-1] * 1.004 ** k for k in range(1, 80)])
    spec = ShadowSpec(hypothesis="T", start=candles[200].open_time, warmup_bars=100)
    run = _run(spec, candles)
    rows = shadow_rows(spec, run)
    *closed, last = rows
    assert last["status"] == OPEN and last["exit_time"] is None and last["exit_reason"] is None
    assert last["mark_price"] == candles[-1].close and last["direction"] == "LONG"
    assert closed and all(r["status"] == CLOSED and r["exit_time"] and r["mark_price"] is None for r in closed)
    assert {r["hypothesis"] for r in rows} == {"T"} and {r["interval"] for r in rows} == {"5m"}


def test_history_window_and_days_to_load():
    spec = ShadowSpec(hypothesis="T", start=CANDLES[500].open_time, warmup_bars=600)
    assert spec.history_start("4h") == spec.start - timedelta(hours=4 * 600)
    assert spec.history_days("1d", spec.start) == 601
    with pytest.raises(ValueError):
        spec.history_start("7x")


def test_empty_history_gives_an_empty_run():
    run = _run(_spec(400), [])
    assert run.trades == [] and run.candles == 0 and shadow_rows(_spec(400), run) == []


def test_l6_config_is_the_declared_one():
    c = l6_lab_config()
    assert c.strategy == "trend_breakout" and c.params == {}
    assert c.symbols == L6_SYMBOLS and len(c.symbols) == 12 and c.intervals == ["4h", "1d"]
    assert c.trailing_stop and c.funding and c.skip_vol == ["HIGH"]
    assert (c.entry_fee_bps, c.exit_fee_bps, c.slippage_bps) == (5.0, 5.0, 2.0)
    assert c.max_position_notional == 2000.0 and c.initial_capital == 3000.0
    assert ShadowSpec(hypothesis="T", start=CANDLES[0].open_time).warmup_bars == 600


# -------------------------------------------------------------- repositório
class _CaptureSession:
    sql = ""

    async def execute(self, stmt):
        self.sql = str(stmt.compile(dialect=postgresql.dialect()))
        raise RuntimeError("só a SQL interessa")


async def test_upsert_overwrites_everything_but_the_key():
    spec = _spec(400)
    rows = shadow_rows(spec, _run(spec, CANDLES))
    session = _CaptureSession()
    with pytest.raises(RuntimeError):
        await ShadowTradeRepository(session).bulk_upsert(rows[:1])  # type: ignore[arg-type]
    sql = session.sql
    assert "ON CONFLICT ON CONSTRAINT uq_shadow_trades_key DO UPDATE" in sql
    assert "status = excluded.status" in sql and "stop_price = excluded.stop_price" in sql
    assert "entry_time = excluded" not in sql and "hypothesis = excluded" not in sql


async def test_upsert_nothing():
    assert await ShadowTradeRepository(_CaptureSession()).bulk_upsert([]) == 0  # type: ignore[arg-type]
