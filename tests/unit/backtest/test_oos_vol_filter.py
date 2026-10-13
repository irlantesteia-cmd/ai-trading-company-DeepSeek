"""L6a: período fora da amostra (data final no passado) e filtro de regime de
volatilidade na entrada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.data import KlineCache, load_candles, load_funding
from app.backtest.engine import BacktestEngine
from app.backtest.lab import LabConfig, run_lab, to_markdown
from app.backtest.types import BacktestConfig
from app.core.enums import MarketType
from scripts.strategy_lab import parse_cli
from tests.unit.backtest.test_data import NOW, NOW_MS, _exchange
from tests.unit.backtest.test_engine import _OneShotStrategy
from tests.unit.strategies.conftest import make_candles

UNTIL = NOW - timedelta(days=1)  # um dia antes do "agora" da exchange falsa
UNTIL_MS = int(UNTIL.timestamp() * 1000)


# ------------------------------------------------------- período fora da amostra
async def test_past_until_returns_nothing_after_it(tmp_path):
    cache = KlineCache(tmp_path)
    async with _exchange(NOW_MS - 5 * 86_400_000, []) as client:
        candles = await load_candles("BTCUSDT", "5m", days=1, now=UNTIL, client=client, cache=cache)
    assert candles and all(c.close_time < UNTIL for c in candles)
    assert candles[0].open_time >= UNTIL - timedelta(days=1)


async def test_past_until_does_not_truncate_a_newer_cache(tmp_path):
    cache = KlineCache(tmp_path)
    async with _exchange(NOW_MS - 10 * 86_400_000, []) as client:
        await load_candles("BTCUSDT", "5m", days=1, now=NOW, client=client, cache=cache)  # cache: ~5 dias até agora
        newest = max(int(r[0]) for r in cache.read("BTCUSDT", "5m"))
        oldest = min(int(r[0]) for r in cache.read("BTCUSDT", "5m"))
        # pedido no passado que começa ANTES do cache: busca o mais antigo e grava
        await load_candles("BTCUSDT", "5m", days=6, now=UNTIL, client=client, cache=cache)
    rows = cache.read("BTCUSDT", "5m")
    assert min(int(r[0]) for r in rows) < oldest            # buscou e gravou o período antigo
    assert max(int(r[0]) for r in rows) == newest           # e não apagou o que vinha depois do `until`


async def test_funding_respects_until(tmp_path):
    import httpx

    step = 8 * 3_600_000
    rows = [{"fundingTime": NOW_MS - k * step, "fundingRate": "0.0001"} for k in range(30, 0, -1)]

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        lo, hi = int(q["startTime"]), int(q["endTime"])
        return httpx.Response(200, json=[r for r in rows if lo <= r["fundingTime"] <= hi][: int(q["limit"])])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await load_funding("BTCUSDT", days=9, now=NOW, client=client, cache=KlineCache(tmp_path))
        past = await load_funding("BTCUSDT", days=3, now=UNTIL, client=client, cache=KlineCache(tmp_path))
    assert past and all(t <= UNTIL_MS for t, _ in past)


# ---------------------------------------------------------- filtro de regime
def _cfg(**kw) -> BacktestConfig:
    return BacktestConfig(initial_capital=Decimal(10000), risk_per_trade_pct=0.01,
                          fee_bps=0.0, slippage_bps=0.0, **kw)


def _run(cfg, candles, regimes):
    return BacktestEngine(cfg).run(symbol="BTCUSDT", interval="5m", market_type=MarketType.FUTURES,
                                   candles=candles, strategy=_OneShotStrategy(trigger_at=10),
                                   regimes=regimes)


def test_vol_filter_skips_entries_signalled_in_high_volatility():
    candles = make_candles([100.0] * 10 + [100.0, 100.0, 100.0, 105.0, 105.0])
    high_at_signal = [("UP", "NORMAL")] * 9 + [("UP", "HIGH")] + [("UP", "NORMAL")] * 5
    # o sinal nasce no fechamento da barra 9 (10 candles; rótulo HIGH) e entraria na 10
    blocked = _run(_cfg(skip_entry_vol=frozenset({"HIGH"})), candles, high_at_signal)
    assert blocked.trades == [] and blocked.metrics["entries_skipped_vol"] == 1
    allowed = _run(_cfg(skip_entry_vol=frozenset({"HIGH"})), candles, [("UP", "NORMAL")] * 15)
    assert len(allowed.trades) == 1
    no_filter = _run(_cfg(), candles, high_at_signal)
    assert len(no_filter.trades) == 1


def test_vol_filter_uses_the_signal_bar_label_not_the_entry_bar():
    candles = make_candles([100.0] * 10 + [100.0, 100.0, 100.0, 105.0, 105.0])
    # HIGH só na barra da entrada (10): o sinal (barra 9) estava NORMAL → entra
    labels = [("UP", "NORMAL")] * 10 + [("UP", "HIGH")] + [("UP", "NORMAL")] * 4
    result = _run(_cfg(skip_entry_vol=frozenset({"HIGH"})), candles, labels)
    assert len(result.trades) == 1


async def test_lab_passes_filter_and_until_and_reports_them():
    candles = make_candles([100 * (1.003 ** k) if (k // 50) % 2 == 0 else 100 * (1.003 ** 50) * (0.997 ** (k % 50))
                            for k in range(900)])

    async def loader(symbol, interval, days):
        return candles

    report = await run_lab(
        LabConfig(strategy="trend_breakout", symbols=["AAA"], intervals=["1h"], trailing_stop=True,
                  max_position_notional=None, random_replicates=20, skip_vol=["HIGH"],
                  until=datetime(2021, 12, 26, tzinfo=UTC)),
        loader=loader,
    )
    assert "sem entrada em volatilidade: HIGH" in to_markdown(report)


def test_cli_until_is_inclusive_and_skip_vol_is_parsed():
    cfg, _, _ = parse_cli(["--strategy", "trend_breakout", "--until", "2021-12-25", "--skip-vol", "high"])
    assert isinstance(cfg, LabConfig)
    assert cfg.until == datetime(2021, 12, 26, tzinfo=UTC)  # 25/12 inteiro
    assert cfg.skip_vol == ["HIGH"]
