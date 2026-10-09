from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.core.enums import MarketRegime, SignalDirection
from app.core.indicators import adx, atr, ema
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.strategies.base import Strategy


class MomentumStrategy(Strategy):
    """Cruzamento EMA(fast)/EMA(slow) confirmado por ADX (regime trending).

    LONG:  fast cruza acima de slow E ADX >= adx_threshold
    SHORT: fast cruza abaixo de slow E ADX >= adx_threshold
    Stop:  entry -+ (stop_atr_mult * ATR)
    Alvo:  entry -+ (target_atr_mult * ATR)
    """

    name = "momentum"

    def __init__(
        self,
        *,
        fast_period: int = 9,
        slow_period: int = 21,
        adx_period: int = 14,
        adx_threshold: float = 20.0,
        atr_period: int = 14,
        stop_atr_mult: float = 1.5,
        target_atr_mult: float = 3.0,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period deve ser < slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult

    @property
    def warmup(self) -> int:
        return max(self.slow_period, self.adx_period * 2, self.atr_period) + 2

    def generate(self, ctx: StrategyContext) -> Signal | None:
        if len(ctx.candles) < self.warmup:
            return None

        closes = ctx.closes
        fast = ema(closes, self.fast_period)
        slow = ema(closes, self.slow_period)
        adx_vals = adx(ctx.highs, ctx.lows, closes, self.adx_period)
        atr_vals = atr(ctx.highs, ctx.lows, closes, self.atr_period)

        i = len(closes) - 1
        f, s = fast[i], slow[i]
        f_prev, s_prev = fast[i - 1], slow[i - 1]
        a = adx_vals[i]
        tr = atr_vals[i]

        if any(v is None for v in (f, s, f_prev, s_prev, a, tr)):
            return None
        assert f is not None and s is not None
        assert f_prev is not None and s_prev is not None
        assert a is not None and tr is not None

        if a < self.adx_threshold or tr <= 0:
            return None

        crossed_up = f_prev <= s_prev and f > s
        crossed_down = f_prev >= s_prev and f < s

        if not (crossed_up or crossed_down):
            return None

        entry = Decimal(str(closes[i]))
        stop_distance = Decimal(str(tr * self.stop_atr_mult))
        target_distance = Decimal(str(tr * self.target_atr_mult))

        if crossed_up:
            direction = SignalDirection.LONG
            stop = entry - stop_distance
            target = entry + target_distance
            regime = MarketRegime.TRENDING_UP
        else:
            direction = SignalDirection.SHORT
            stop = entry + stop_distance
            target = entry - target_distance
            regime = MarketRegime.TRENDING_DOWN

        confidence = min(1.0, a / 50.0)

        return Signal(
            signal_id=str(uuid4()),
            symbol=ctx.symbol,
            market_type=ctx.market_type,
            direction=direction,
            confidence=confidence,
            horizon=ctx.interval,
            suggested_entry=entry,
            suggested_stop=stop,
            suggested_target=target,
            regime=regime,
            strategy=self.name,
            agent=f"strategy::{self.name}",
            rationale=(
                f"EMA({self.fast_period})/EMA({self.slow_period}) cross, "
                f"ADX={a:.2f}, ATR={tr:.4f}"
            ),
            generated_at=datetime.now(UTC),
        )