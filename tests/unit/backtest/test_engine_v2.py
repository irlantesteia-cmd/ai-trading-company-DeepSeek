"""Motor v2 do laboratório: regras do bot ao vivo + sanidade estatística."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.types import BacktestConfig
from app.core.enums import MarketType, SignalDirection
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.strategies.base import Strategy
from tests.unit.backtest.test_engine import _OneShotStrategy
from tests.unit.strategies.conftest import make_candles


def _cfg(**kw) -> BacktestConfig:
    base = {"initial_capital": Decimal(10000), "risk_per_trade_pct": 0.01,
            "fee_bps": 0.0, "slippage_bps": 0.0}
    return BacktestConfig(**{**base, **kw})


def _run(cfg, candles, strategy):
    return BacktestEngine(cfg).run(
        symbol="BTCUSDT", interval="5m", market_type=MarketType.FUTURES,
        candles=candles, strategy=strategy,
    )


class _PctStrategy(Strategy):
    """Entra sempre que pode: LONG (ou direção aleatória) com stop/alvo em %."""

    name = "pct"

    def __init__(self, *, stop_pct=1.0, target_pct=1.0, rng: random.Random | None = None):
        self.stop, self.target, self.rng = stop_pct / 100, target_pct / 100, rng

    def generate(self, ctx: StrategyContext) -> Signal | None:
        entry = ctx.candles[-1].close
        long = True if self.rng is None else self.rng.random() < 0.5
        sign = Decimal(1) if long else Decimal(-1)
        return Signal(
            signal_id=str(uuid4()), symbol=ctx.symbol, market_type=ctx.market_type,
            direction=SignalDirection.LONG if long else SignalDirection.SHORT,
            confidence=0.9, horizon=ctx.interval, suggested_entry=entry,
            suggested_stop=entry * (1 - sign * Decimal(str(self.stop))),
            suggested_target=entry * (1 + sign * Decimal(str(self.target))),
            strategy=self.name, agent="test", generated_at=datetime.now(UTC),
        )


class _WindowProbe(Strategy):
    name = "probe"

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def generate(self, ctx: StrategyContext) -> Signal | None:
        self.sizes.append(len(ctx.candles))
        return None


def _walk(n: int, *, drift: float = 0.0, vol: float = 0.004, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= 1 + drift + rng.gauss(0, vol)
        out.append(price)
    return out


# --------------------------------------------------------- regras do bot
def test_strategy_sees_only_the_lookback_window():
    probe = _WindowProbe()
    _run(_cfg(lookback=50), make_candles([100.0] * 120), probe)
    assert max(probe.sizes) == 50
    assert probe.sizes[:3] == [1, 2, 3]


def test_time_exit_closes_after_n_bars():
    candles = make_candles([100.0] * 30)  # preço parado: nem stop nem alvo
    result = _run(_cfg(max_holding_bars=6), candles, _OneShotStrategy(trigger_at=10))
    (trade,) = result.trades
    assert trade.exit_reason == "time"
    assert trade.bars_held == 6
    assert result.metrics["exits_time"] == 1


def test_position_notional_is_capped():
    # risco 100 / distância 1 → 100 unidades (10.000 de notional); teto 2.000
    candles = make_candles([100.0] * 10 + [100.0, 105.0] + [105.0] * 4)
    result = _run(_cfg(max_position_notional=Decimal(2000)), candles, _OneShotStrategy(trigger_at=10))
    assert result.trades[0].quantity == Decimal(20)


def test_levels_are_anchored_to_fill_price():
    # Sinal no close 100; a barra seguinte abre em 102 (gap).
    closes = [100.0] * 10 + [102.0] * 6
    candles = make_candles(closes)
    candles[10] = candles[10].model_copy(update={"open": Decimal(102)})
    result = _run(_cfg(), candles, _OneShotStrategy(trigger_at=10))
    trade = result.trades[0]
    assert trade.entry_price == Decimal(102)
    assert trade.stop_price == Decimal(101)    # 100-1, deslocado +2
    assert trade.target_price == Decimal(105)  # 100+3, deslocado +2

    raw = _run(_cfg(anchor_to_fill=False), candles, _OneShotStrategy(trigger_at=10)).trades[0]
    assert raw.stop_price == Decimal(99)


def test_entry_and_exit_fees_can_differ():
    candles = make_candles([100.0] * 10 + [100.0, 105.0] + [105.0] * 4)
    maker_in = _run(_cfg(entry_fee_bps=2.0, exit_fee_bps=5.0), candles, _OneShotStrategy(trigger_at=10))
    taker_both = _run(_cfg(fee_bps=5.0), candles, _OneShotStrategy(trigger_at=10))
    assert maker_in.trades[0].fees < taker_both.trades[0].fees
    # 100 un. × 100 × 2 bps + 100 un. × 103 × 5 bps
    assert maker_in.trades[0].fees == Decimal("2.0") + Decimal("5.15")


def test_gross_excludes_costs_and_net_includes_them():
    candles = make_candles([100.0] * 10 + [100.0, 105.0] + [105.0] * 4)
    result = _run(_cfg(fee_bps=5.0, slippage_bps=2.0), candles, _OneShotStrategy(trigger_at=10))
    trade = result.trades[0]
    # Alvo ancorado no fill com derrapagem (100,02 → 103,02); bruto = 100 → 103,02.
    assert trade.gross_return_pct == pytest.approx(3.02)
    assert trade.return_pct < trade.gross_return_pct
    assert result.metrics["avg_cost_pct"] == pytest.approx(
        trade.gross_return_pct - trade.return_pct
    )


# ------------------------------------------------- sanidade do laboratório
def test_random_signals_have_no_gross_edge_and_lose_the_costs():
    """Sem informação, o bruto fica ~0 e o líquido ~ -custo (0,14%/trade)."""
    candles = make_candles(_walk(6000))
    result = _run(
        _cfg(fee_bps=5.0, slippage_bps=2.0),
        candles,
        _PctStrategy(stop_pct=0.8, target_pct=0.8, rng=random.Random(1)),
    )
    m = result.metrics
    assert m["num_trades"] > 300
    assert abs(m["avg_gross_return_pct"]) < 0.08
    assert m["avg_cost_pct"] == pytest.approx(0.14, abs=0.01)
    assert m["avg_return_pct"] < 0


def test_known_trend_is_detected_as_edge():
    """Série com deriva positiva: comprar sempre tem vantagem bruta clara."""
    candles = make_candles(_walk(6000, drift=0.0008))
    result = _run(_cfg(fee_bps=5.0, slippage_bps=2.0), candles, _PctStrategy(stop_pct=1.0, target_pct=1.0))
    m = result.metrics
    assert m["num_trades"] > 300
    assert m["avg_gross_return_pct"] > 0.2
    assert m["avg_return_pct"] > 0
