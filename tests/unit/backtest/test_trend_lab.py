"""L3a: stop móvel, funding no backtest e estratégia de rompimento Donchian."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.backtest.data import KlineCache, load_funding
from app.backtest.engine import BacktestEngine
from app.backtest.lab import LabConfig, run_lab, to_markdown
from app.backtest.stats import summarize
from app.backtest.types import BacktestConfig
from app.core.enums import MarketType, SignalDirection
from app.domain.models.strategy_context import StrategyContext
from app.strategies.trend_breakout import TrendBreakoutStrategy
from tests.unit.backtest.test_engine import _OneShotStrategy
from tests.unit.strategies.conftest import make_candles

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _cfg(**kw) -> BacktestConfig:
    base = {"initial_capital": Decimal(10000), "risk_per_trade_pct": 0.01,
            "fee_bps": 0.0, "slippage_bps": 0.0}
    return BacktestConfig(**{**base, **kw})


def _run(cfg, candles, strategy, **kw):
    return BacktestEngine(cfg).run(
        symbol="BTCUSDT", interval="5m", market_type=MarketType.FUTURES,
        candles=candles, strategy=strategy, **kw,
    )


def _no_target(direction=SignalDirection.LONG) -> _OneShotStrategy:
    # Alvo muito longe: na prática só o stop (móvel) encerra o trade.
    return _OneShotStrategy(trigger_at=10, direction=direction,
                            stop_offset=Decimal(1), target_offset=Decimal(1000))


# -------------------------------------------------------------- stop móvel
def test_long_trailing_stop_locks_in_gains():
    # entra a 100 (stop 99); sobe até 110 → stop vai a 109; cai → sai a 109
    closes = [100.0] * 10 + [100.0, 104.0, 108.0, 110.0, 105.0, 100.0, 100.0]
    result = _run(_cfg(trailing_stop=True), make_candles(closes), _no_target())
    (trade,) = result.trades
    assert trade.exit_reason == "stop"
    # high da barra de 110 = 110 × 1,001 (make_candles) → stop = 110,11 - 1
    assert trade.exit_price == pytest.approx(Decimal("109.11"), abs=Decimal("0.01"))
    assert trade.pnl > 0


def test_short_trailing_stop_mirrors_long():
    closes = [100.0] * 10 + [100.0, 96.0, 92.0, 90.0, 95.0, 100.0, 100.0]
    result = _run(_cfg(trailing_stop=True), make_candles(closes), _no_target(SignalDirection.SHORT))
    (trade,) = result.trades
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(Decimal("90.91"), abs=Decimal("0.01"))  # 90×0,999 + 1
    assert trade.pnl > 0


def test_trailing_stop_never_loosens_and_is_off_by_default():
    closes = [100.0] * 10 + [100.0, 105.0, 99.5, 99.0, 98.0]
    trailed = _run(_cfg(trailing_stop=True), make_candles(closes), _no_target()).trades[0]
    fixed = _run(_cfg(), make_candles(closes), _no_target()).trades[0]
    assert trailed.exit_price > fixed.exit_price  # o móvel saiu antes, com o stop mais alto
    assert fixed.exit_price == Decimal(99)


# ----------------------------------------------------------------- funding
def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_long_pays_and_short_receives_funding_inside_the_trade():
    closes = [100.0] * 10 + [100.0, 100.0, 100.0, 105.0, 105.0]  # alvo 103 na barra 13
    candles = make_candles(closes)
    entry, exit_ = candles[10].open_time, candles[13].close_time
    rates = [
        (_ms(entry - timedelta(minutes=1)), 0.5),   # antes da entrada: ignora
        (_ms(entry + timedelta(minutes=7)), 0.001),
        (_ms(entry + timedelta(minutes=12)), 0.002),
        (_ms(exit_ + timedelta(minutes=1)), 0.5),   # depois da saída: ignora
    ]
    long_ = _run(_cfg(), candles, _OneShotStrategy(trigger_at=10), funding_rates=rates).trades[0]
    # notional 100 un. × 100 = 10.000 → 0,3% = 30 USDT pagos
    assert long_.funding == Decimal(30)
    assert long_.funding_pct == pytest.approx(0.3)
    assert long_.pnl == Decimal(300) - Decimal(30)

    closes_s = [100.0] * 10 + [100.0, 100.0, 100.0, 95.0, 95.0]
    short = _run(
        _cfg(), make_candles(closes_s), _OneShotStrategy(trigger_at=10, direction=SignalDirection.SHORT),
        funding_rates=rates,
    ).trades[0]
    assert short.funding == Decimal(-30)  # vendido recebe


def test_stats_split_cost_and_funding():
    closes = [100.0] * 10 + [100.0, 100.0, 100.0, 105.0, 105.0]
    candles = make_candles(closes)
    rates = [(_ms(candles[11].open_time), 0.002)]
    trade = _run(_cfg(fee_bps=5.0), candles, _OneShotStrategy(trigger_at=10), funding_rates=rates).trades[0]
    s = summarize([trade])
    assert s.avg_funding_pct == pytest.approx(0.2)
    assert s.avg_gross_pct - s.avg_cost_pct - s.avg_funding_pct == pytest.approx(s.avg_net_pct)


# --------------------------------------------------------------- estratégia
def _ctx(closes: list[float]) -> StrategyContext:
    return StrategyContext(symbol="BTCUSDT", market_type=MarketType.FUTURES,
                           interval="1d", candles=make_candles(closes))


def test_breakout_long_short_and_inside_channel():
    s = TrendBreakoutStrategy(channel_period=5, atr_period=5, stop_atr_mult=3)
    base = [100.0, 101.0, 99.0, 100.5, 99.5] * 3
    up = s.generate(_ctx([*base, 106.0]))
    down = s.generate(_ctx([*base, 94.0]))
    assert up is not None and up.direction is SignalDirection.LONG
    assert up.suggested_target is None and up.suggested_stop < up.suggested_entry
    assert down is not None and down.direction is SignalDirection.SHORT
    assert down.suggested_stop > down.suggested_entry
    assert s.generate(_ctx([*base, 100.2])) is None


def test_breakout_channel_excludes_the_current_bar():
    """Se a barra atual entrasse no canal, nenhum fechamento romperia a própria máxima."""
    s = TrendBreakoutStrategy(channel_period=3, atr_period=3)
    assert s.generate(_ctx([100.0, 100.0, 100.0, 100.0, 100.0, 103.0])) is not None


def test_breakout_validates_channel():
    with pytest.raises(ValueError):
        TrendBreakoutStrategy(channel_period=1)


# ------------------------------------------------------- carregador funding
async def test_load_funding_caches_and_fetches_incrementally(tmp_path):
    step = 8 * 3_600_000
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    all_rows = [{"fundingTime": _ms(now) - k * step, "fundingRate": str(k / 1e5)} for k in range(30, 0, -1)]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        calls.append(dict(q))
        lo, hi = int(q["startTime"]), int(q["endTime"])
        return httpx.Response(200, json=[r for r in all_rows if lo <= r["fundingTime"] <= hi][: int(q["limit"])])

    cache = KlineCache(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await load_funding("BTCUSDT", days=5, now=now, client=client, cache=cache)
        n_calls = len(calls)
        again = await load_funding("BTCUSDT", days=5, now=now, client=client, cache=cache)

    assert len(first) == 15 and first == sorted(first)  # 5 dias × 3 liquidações
    assert again == first and len(calls) == n_calls       # 2ª carga só do cache


# ------------------------------------------------------------------- lab
async def test_lab_runs_trend_breakout_with_trailing_and_funding():
    closes = [100 * (1.002 ** k) if (k // 60) % 2 == 0 else 100 * (1.002 ** 60) * (0.998 ** (k % 60))
              for k in range(600)]
    candles = make_candles(closes)
    rates = [(_ms(c.open_time), 0.0001) for c in candles[::96]]

    async def loader(symbol, interval, days):
        return candles

    async def funding_loader(symbol, days):
        return rates

    report = await run_lab(
        LabConfig(strategy="trend_breakout", symbols=["AAA"], intervals=["1h"],
                  trailing_stop=True, funding=True, params={"channel_period": 20.0},
                  max_position_notional=None),
        loader=loader, funding_loader=funding_loader,
    )
    assert report.overall.trades > 0
    md = to_markdown(report)
    assert "Funding/trade" in md and "stop móvel: sim" in md and "funding: real" in md
