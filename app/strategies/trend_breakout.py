from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.core.enums import MarketRegime, SignalDirection
from app.core.indicators import atr
from app.domain.models.signal import Signal
from app.domain.models.strategy_context import StrategyContext
from app.strategies.base import Strategy


class TrendBreakoutStrategy(Strategy):
    """Rompimento de canal Donchian (modelo "Turtle"), para horizonte longo.

    - LONG quando o fechamento passa da **máxima** das `channel_period` barras
      anteriores; SHORT quando passa da **mínima**. A barra atual não entra no
      canal (sem olhar o futuro).
    - Stop inicial a `stop_atr_mult` × ATR; **sem alvo**: a saída é pelo stop,
      que no laboratório vira stop móvel (`trailing_stop`).

    Ainda não vai ao bot ao vivo: depende de stop móvel na Binance e de
    funding no risco, e só depois de aprovada pelo gate do laboratório.
    """

    name = "trend_breakout"

    def __init__(
        self,
        *,
        channel_period: int = 20,
        atr_period: int = 20,
        stop_atr_mult: float = 3.0,
    ) -> None:
        if channel_period < 2:
            raise ValueError("channel_period deve ser >= 2")
        self.channel_period = channel_period
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult

    @property
    def warmup(self) -> int:
        return max(self.channel_period, self.atr_period) + 2

    def generate(self, ctx: StrategyContext) -> Signal | None:
        if len(ctx.candles) < self.warmup:
            return None
        highs, lows, closes = ctx.highs, ctx.lows, ctx.closes
        i = len(closes) - 1
        tr = atr(highs, lows, closes, self.atr_period)[i]
        if tr is None or tr <= 0:
            return None

        window = slice(i - self.channel_period, i)  # barras anteriores, sem a atual
        upper, lower = max(highs[window]), min(lows[window])
        close = closes[i]
        if close > upper:
            direction, sign, regime = SignalDirection.LONG, -1, MarketRegime.TRENDING_UP
        elif close < lower:
            direction, sign, regime = SignalDirection.SHORT, 1, MarketRegime.TRENDING_DOWN
        else:
            return None

        entry = Decimal(str(close))
        stop = entry + sign * Decimal(str(tr * self.stop_atr_mult))
        return Signal(
            signal_id=str(uuid4()),
            symbol=ctx.symbol,
            market_type=ctx.market_type,
            direction=direction,
            confidence=0.8,
            horizon=ctx.interval,
            suggested_entry=entry,
            suggested_stop=stop,
            suggested_target=None,
            regime=regime,
            strategy=self.name,
            agent=f"strategy::{self.name}",
            rationale=(
                f"Donchian({self.channel_period}) rompido: close={close:.6g} "
                f"canal=[{lower:.6g}, {upper:.6g}], ATR={tr:.6g}"
            ),
            generated_at=datetime.now(UTC),
        )
