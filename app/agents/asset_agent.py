from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.agents.base import BaseAgent, EventHandler
from app.core.enums import (
    AgentRole,
    MarketRegime,
    MarketType,
    SignalDirection,
)
from app.domain.models.market import Candle
from app.domain.models.signal import Signal
from app.events.event import CandleClosed, Event, SignalGenerated
from app.strategies.base import Strategy
from app.strategies.context import StrategyContext

logger = logging.getLogger(__name__)


class AssetAgent(BaseAgent):
    """Agente especializado em um único ativo.

    Se `ref_symbol` for definido, `analyze()` também busca candles do
    ativo de referência (ex.: BTC para alts) e os inclui em
    `StrategyContext.ref_candles` — permitindo features cross-asset.
    """

    role = AgentRole.ASSET

    def __init__(
        self,
        context,
        *,
        symbol: str,
        market_type: MarketType = MarketType.FUTURES,
        interval: str = "5m",
        strategy: Strategy | None = None,
        lookback: int = 200,
        ref_symbol: str | None = None,
    ) -> None:
        super().__init__(context)
        self.symbol = symbol
        self.market_type = market_type
        self.interval = interval
        self.strategy = strategy
        self.lookback = lookback
        self.ref_symbol = ref_symbol
        self.name = f"asset::{symbol}"

    # ------------------------------------------------------------- análise
    async def analyze(self) -> Signal | None:
        if self.strategy is None:
            return None
        candles = await self._fetch_candles()
        if len(candles) < self.strategy.warmup:
            return None

        ref_candles: dict[str, list[Candle]] = {}
        if self.ref_symbol is not None:
            try:
                ref = await self._fetch_candles_for(self.ref_symbol)
            except Exception:
                logger.exception(
                    "asset.ref_fetch_failed",
                    extra={"symbol": self.symbol, "ref_symbol": self.ref_symbol},
                )
                return None
            if not ref:
                logger.warning(
                    "asset.ref_empty",
                    extra={"symbol": self.symbol, "ref_symbol": self.ref_symbol},
                )
                return None
            ref_candles[self.ref_symbol] = ref

        ctx = StrategyContext(
            symbol=self.symbol,
            market_type=self.market_type,
            interval=self.interval,
            candles=candles,
            ref_candles=ref_candles,
        )
        return self.strategy.generate(ctx)

    async def _fetch_candles(self) -> list[Candle]:
        return await self._fetch_candles_for(self.symbol)

    async def _fetch_candles_for(self, symbol: str) -> list[Candle]:
        """Candles fechados apenas: o REST inclui a barra em andamento no fim,
        e o modelo foi treinado só com barras fechadas."""
        candles = await self.context.exchange.market_data.get_candles(
            symbol,
            self.interval,
            self.market_type,
            limit=self.lookback,
        )
        return [c for c in candles if c.closed]

    # ------------------------------------------------------ subscriptions
    def subscriptions(self) -> dict[type[Event], EventHandler]:
        return {CandleClosed: self._on_candle}

    async def _on_candle(self, event: Event) -> None:
        if not isinstance(event, CandleClosed):
            return
        if event.symbol != self.symbol or event.interval != self.interval:
            return
        await self.tick()

    async def tick(self) -> Signal | None:
        signal = await self.analyze()
        if signal is None:
            return None
        await self.context.event_bus.publish(
            SignalGenerated(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                agent=signal.agent,
                direction=signal.direction.value,
                confidence=signal.confidence,
                signal=signal,
            )
        )
        return signal

    # ---------------------------------------------------------------- util
    def _build_signal(
        self,
        *,
        direction: SignalDirection,
        confidence: float,
        rationale: str,
        strategy: str = "default",
        regime: MarketRegime = MarketRegime.UNKNOWN,
    ) -> Signal:
        return Signal(
            signal_id=str(uuid4()),
            symbol=self.symbol,
            market_type=self.market_type,
            direction=direction,
            confidence=confidence,
            horizon=self.interval,
            regime=regime,
            strategy=strategy,
            agent=self.name,
            rationale=rationale,
            generated_at=datetime.now(UTC),
        )