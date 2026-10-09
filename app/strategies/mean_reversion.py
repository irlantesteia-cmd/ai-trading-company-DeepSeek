from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.core.enums import MarketRegime, SignalDirection
from app.core.indicators import adx, atr, sma, zscore
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    """Reversão à média via z-score.

    LONG:  z <= -entry_z E ADX < adx_threshold
    SHORT: z >= +entry_z E ADX < adx_threshold
    Alvo:  SMA(lookback)
    Stop:  entry -+ (stop_atr_mult * ATR)
    """

    name = "mean_reversion"

    def __init__(
        self,
        *,
        lookback: int = 20,
        entry_z: float = 2.0,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        atr_period: int = 14,
        stop_atr_mult: float = 2.0,
    ) -> None:
        self.lookback = lookback
        self.entry_z = entry_z
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult

    @property
    def warmup(self) -> int:
        return max(self.lookback, self.adx_period * 2, self.atr_period) + 2

    def generate(self, ctx: StrategyContext) -> Signal | None:
        if len(ctx.candles) < self.warmup:
            return None

        closes = ctx.closes
        z = zscore(closes, self.lookback)
        m = sma(closes, self.lookback)
        adx_vals = adx(ctx.highs, ctx.lows, closes, self.adx_period)
        atr_vals = atr(ctx.highs, ctx.lows, closes, self.atr_period)

        i = len(closes) - 1
        zi, mi, ai, tri = z[i], m[i], adx_vals[i], atr_vals[i]
        if any(v is None for v in (zi, mi, ai, tri)):
            return None
        assert zi is not None and mi is not None
        assert ai is not None and tri is not None

        if ai >= self.adx_threshold or tri <= 0:
            return None

        entry = Decimal(str(closes[i]))
        stop_distance = Decimal(str(tri * self.stop_atr_mult))

        if zi <= -self.entry_z:
            direction = SignalDirection.LONG
            stop = entry - stop_distance
            target = Decimal(str(mi))
        elif zi >= self.entry_z:
            direction = SignalDirection.SHORT
            stop = entry + stop_distance
            target = Decimal(str(mi))
        else:
            return None

        confidence = min(1.0, abs(zi) / 4.0)

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
            regime=MarketRegime.RANGING,
            strategy=self.name,
            agent=f"strategy::{self.name}",
            rationale=(
                f"z={zi:.2f} (lookback={self.lookback}), "
                f"SMA={mi:.4f}, ADX={ai:.2f}, ATR={tri:.4f}"
            ),
            generated_at=datetime.now(UTC),
        )