from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.core.enums import MarketRegime, SignalDirection
from app.domain.models.signal import Signal
from app.features.pipeline import FeaturePipeline
from app.ml.model import ForwardReturnClassifier
from app.strategies.context import StrategyContext


class MLSignalGenerator:
    """Converte P(close[t+h] > close[t]) em `Signal`.

    - proba >= long_threshold  → LONG
    - proba <= short_threshold → SHORT
    - caso contrário           → None

    O pipeline pode ser um `FeaturePipeline` puro ou um
    `CrossAssetPipeline` (ML-3d-1). Nesse segundo caso, `ctx.ref_candles`
    precisa estar populado pelo chamador (o `AssetAgent` garante isso
    quando `ref_symbol` é definido).
    """

    name = "ml_classifier"

    def __init__(
        self,
        model: ForwardReturnClassifier,
        pipeline: FeaturePipeline,
        *,
        long_threshold: float = 0.6,
        short_threshold: float = 0.4,
        stop_atr_mult: float = 1.5,
        target_atr_mult: float = 2.5,
    ) -> None:
        if not 0.0 <= short_threshold < long_threshold <= 1.0:
            raise ValueError("exige 0 <= short < long <= 1")
        self._model = model
        self._pipeline = pipeline
        self._long = long_threshold
        self._short = short_threshold
        self._stop_mult = stop_atr_mult
        self._target_mult = target_atr_mult

    def generate(self, ctx: StrategyContext) -> Signal | None:
        # Propaga ref_candles para permitir features cross-asset.
        # FeaturePipeline puro ignora; CrossAssetPipeline consome.
        fm = self._pipeline.transform(ctx.candles, ctx.ref_candles)
        if fm.values.shape[0] == 0:
            return None

        last_x = fm.values[-1:]
        proba = float(self._model.proba_up(last_x)[0])

        if proba >= self._long:
            direction = SignalDirection.LONG
            confidence = proba
            regime = MarketRegime.TRENDING_UP
        elif proba <= self._short:
            direction = SignalDirection.SHORT
            confidence = 1.0 - proba
            regime = MarketRegime.TRENDING_DOWN
        else:
            return None

        last_close = Decimal(str(float(ctx.candles[-1].close)))
        atr_val = self._compute_last_atr(ctx)
        stop_distance = atr_val * Decimal(str(self._stop_mult))
        target_distance = atr_val * Decimal(str(self._target_mult))

        if direction == SignalDirection.LONG:
            stop = last_close - stop_distance
            target = last_close + target_distance
        else:
            stop = last_close + stop_distance
            target = last_close - target_distance

        return Signal(
            signal_id=str(uuid4()),
            symbol=ctx.symbol,
            market_type=ctx.market_type,
            direction=direction,
            confidence=confidence,
            horizon=ctx.interval,
            suggested_entry=last_close,
            suggested_stop=stop,
            suggested_target=target,
            regime=regime,
            strategy=self.name,
            agent=f"ml::{self.name}",
            rationale=f"proba_up={proba:.4f} (long>={self._long}, short<={self._short})",
            generated_at=datetime.now(UTC),
        )

    @staticmethod
    def _compute_last_atr(ctx: StrategyContext) -> Decimal:
        from app.strategies.indicators import atr

        highs = [float(c.high) for c in ctx.candles]
        lows = [float(c.low) for c in ctx.candles]
        closes = [float(c.close) for c in ctx.candles]
        values = atr(highs, lows, closes, 14)
        last = values[-1]
        if last is None or last <= 0:
            # Fallback: 1% do último close.
            return Decimal(str(float(closes[-1]) * 0.01))
        return Decimal(str(last))