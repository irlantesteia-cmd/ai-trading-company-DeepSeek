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
from tests.unit.strategies.conftest import make_candles


class _OneShotStrategy(Strategy):
    """Gera UM sinal exatamente quando len(candles) == trigger_at (1-indexed)."""

    name = "one_shot"

    def __init__(
        self,
        *,
        trigger_at: int,
        direction: SignalDirection = SignalDirection.LONG,
        stop_offset: Decimal = Decimal(1),
        target_offset: Decimal = Decimal(3),
    ) -> None:
        self.trigger_at = trigger_at
        self.direction = direction
        self.stop_offset = stop_offset
        self.target_offset = target_offset

    @property
    def warmup(self) -> int:
        return self.trigger_at

    def generate(self, ctx: StrategyContext) -> Signal | None:
        if len(ctx.candles) != self.trigger_at:
            return None
        entry = ctx.candles[-1].close
        if self.direction == SignalDirection.LONG:
            stop = entry - self.stop_offset
            target = entry + self.target_offset
        else:
            stop = entry + self.stop_offset
            target = entry - self.target_offset
        return Signal(
            signal_id=str(uuid4()),
            symbol=ctx.symbol,
            market_type=ctx.market_type,
            direction=self.direction,
            confidence=0.9,
            horizon=ctx.interval,
            suggested_entry=entry,
            suggested_stop=stop,
            suggested_target=target,
            strategy=self.name,
            agent="test",
            generated_at=datetime.now(UTC),
        )


class _NeverStrategy(Strategy):
    name = "never"

    def generate(self, ctx: StrategyContext) -> Signal | None:
        return None


def _engine_no_costs() -> BacktestEngine:
    return BacktestEngine(
        BacktestConfig(
            initial_capital=Decimal(10000),
            risk_per_trade_pct=0.01,
            fee_bps=0.0,
            slippage_bps=0.0,
        )
    )


def _run(candles, strategy):
    return _engine_no_costs().run(
        symbol="BTCUSDT",
        interval="5m",
        market_type=MarketType.FUTURES,
        candles=candles,
        strategy=strategy,
    )


def test_target_hit_closes_with_profit():
    closes = [100.0] * 10 + [100.0, 100.0, 105.0, 105.0, 105.0, 105.0]
    candles = make_candles(closes)
    strategy = _OneShotStrategy(
        trigger_at=10,
        stop_offset=Decimal(1),
        target_offset=Decimal(3),
    )
    result = _run(candles, strategy)

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == "target"
    assert t.exit_price == Decimal(103)  # 100 + 3
    assert t.quantity == Decimal(100)    # risco 100 USD / 1 de distância
    assert t.pnl == Decimal(300)         # (103-100)*100
    assert result.final_equity == Decimal(10300)
    assert result.total_return_pct == pytest.approx(3.0)


def test_stop_hit_closes_with_loss():
    closes = [100.0] * 10 + [100.0, 98.0, 98.0, 98.0, 98.0, 98.0]
    candles = make_candles(closes)
    strategy = _OneShotStrategy(
        trigger_at=10,
        stop_offset=Decimal(1),
        target_offset=Decimal(3),
    )
    result = _run(candles, strategy)

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == "stop"
    assert t.exit_price == Decimal(99)   # 100 - 1
    assert t.quantity == Decimal(100)
    assert t.pnl == Decimal(-100)        # (99-100)*100
    assert result.final_equity == Decimal(9900)


def test_short_target_hit():
    closes = [100.0] * 10 + [100.0, 100.0, 95.0, 95.0, 95.0, 95.0]
    candles = make_candles(closes)
    strategy = _OneShotStrategy(
        trigger_at=10,
        direction=SignalDirection.SHORT,
        stop_offset=Decimal(1),
        target_offset=Decimal(3),
    )
    result = _run(candles, strategy)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == "target"
    assert t.exit_price == Decimal(97)
    assert t.pnl == Decimal(300)


def test_no_signal_no_trades():
    closes = [100.0] * 30
    candles = make_candles(closes)
    result = _run(candles, _NeverStrategy())
    assert result.trades == []
    assert result.final_equity == Decimal(10000)


def test_fees_and_slippage_reduce_pnl():
    closes = [100.0] * 10 + [100.0, 100.0, 105.0, 105.0, 105.0, 105.0]
    candles = make_candles(closes)
    strategy = _OneShotStrategy(
        trigger_at=10,
        stop_offset=Decimal(1),
        target_offset=Decimal(3),
    )

    clean = BacktestEngine(
        BacktestConfig(
            initial_capital=Decimal(10000),
            risk_per_trade_pct=0.01,
            fee_bps=0.0,
            slippage_bps=0.0,
        )
    ).run(
        symbol="BTCUSDT",
        interval="5m",
        market_type=MarketType.FUTURES,
        candles=candles,
        strategy=strategy,
    )

    costly = BacktestEngine(
        BacktestConfig(
            initial_capital=Decimal(10000),
            risk_per_trade_pct=0.01,
            fee_bps=10.0,       # 10 bps
            slippage_bps=5.0,   # 5 bps
        )
    ).run(
        symbol="BTCUSDT",
        interval="5m",
        market_type=MarketType.FUTURES,
        candles=candles,
        strategy=strategy,
    )

    assert costly.final_equity < clean.final_equity


def test_empty_candles_raises():
    with pytest.raises(ValueError):
        _run([], _OneShotStrategy(trigger_at=1))