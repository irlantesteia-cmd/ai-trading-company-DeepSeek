"""Stream de eventos `CandleClosed` via polling de klines.

Uma instância por símbolo. A cada `poll_interval_s`, busca os candles mais
recentes via `get_candles(limit=3)` e publica `CandleClosed` no event bus
para a barra mais recente **já fechada** — uma única vez por `open_time`.

Esta é a ponte que faltava: sem ela, o `AssetAgent` reage a `CandleClosed`
mas ninguém publica o evento, então os agentes por ativo ficam inertes.

Detecção de barra fechada: comparamos `candle.close_time <= now`. O candle
em aberto tem `close_time` no futuro (Binance retorna a barra corrente com
`close_time` previsto), então é naturalmente excluído.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import settings
from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.events.bus import EventBus
from app.events.event import CandleClosed
from app.exchanges.base.exchange import Exchange

logger = logging.getLogger(__name__)


class CandleStreamService:
    """Publica `CandleClosed` quando uma barra nova fecha.

    Estado local: `_last_emitted_open_time`. Idempotente por
    `(symbol, interval, open_time)` implícito — chamar `poll_once()`
    várias vezes com o mesmo resultado de `get_candles` publica só uma vez.

    `poll_once()` deixa exceções propagarem (útil para testes); `run_forever()`
    captura e loga — falha de um ciclo não mata o loop.
    """

    def __init__(
        self,
        *,
        exchange: Exchange,
        event_bus: EventBus,
        symbol: str,
        interval: str,
        market_type: MarketType,
        poll_interval_s: float = 30.0,
        max_emit_lag_s: float | None = None,
    ) -> None:
        """`max_emit_lag_s`: candle fechado há mais tempo que isto não é
        emitido (default `settings.candle_max_emit_lag_s`; <= 0 desliga)."""
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s deve ser > 0")
        self._max_emit_lag_s = (
            settings.candle_max_emit_lag_s if max_emit_lag_s is None else max_emit_lag_s
        )
        self._exchange = exchange
        self._event_bus = event_bus
        self._symbol = symbol
        self._interval = interval
        self._market_type = market_type
        self._poll_interval_s = poll_interval_s
        self._last_emitted_open_time: datetime | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def interval(self) -> str:
        return self._interval

    @property
    def last_emitted_open_time(self) -> datetime | None:
        return self._last_emitted_open_time

    async def poll_once(self, *, now: datetime | None = None) -> Candle | None:
        """Um ciclo. Retorna o candle emitido ou None.

        Levanta em caso de falha de exchange — `run_forever` protege.
        """
        now = now or datetime.now(UTC)

        candles = await self._exchange.market_data.get_candles(
            symbol=self._symbol,
            interval=self._interval,
            market_type=self._market_type,
            limit=3,
        )
        if not candles:
            return None

        closed = [c for c in candles if c.close_time <= now]
        if not closed:
            return None

        latest = max(closed, key=lambda c: c.open_time)
        if (
            self._last_emitted_open_time is not None
            and latest.open_time <= self._last_emitted_open_time
        ):
            return None

        lag_s = (now - latest.close_time).total_seconds()
        if 0 < self._max_emit_lag_s < lag_s:
            # Candle velho (volta de suspensão do PC, boot, rede lenta): um
            # sinal calculado agora já nasceria atrasado. Marca como visto
            # e espera o próximo fechamento.
            self._last_emitted_open_time = latest.open_time
            logger.warning(
                "candle_stream.stale_skipped",
                extra={
                    "symbol": latest.symbol,
                    "interval": latest.interval,
                    "open_time": latest.open_time.isoformat(),
                    "lag_s": round(lag_s, 1),
                    "max_lag_s": self._max_emit_lag_s,
                },
            )
            return None

        await self._event_bus.publish(
            CandleClosed(
                symbol=latest.symbol,
                interval=latest.interval,
                close=float(latest.close),
            )
        )
        self._last_emitted_open_time = latest.open_time
        logger.info(
            "candle_stream.emitted",
            extra={
                "symbol": latest.symbol,
                "interval": latest.interval,
                "open_time": latest.open_time.isoformat(),
                "close": str(latest.close),
            },
        )
        return latest

    async def run_forever(self) -> None:
        """Loop infinito: `poll_once` + sleep. Falhas são absorvidas."""
        while True:
            try:
                await self.poll_once()
            except Exception:
                logger.exception(
                    "candle_stream.poll_failed",
                    extra={"symbol": self._symbol, "interval": self._interval},
                )
            await asyncio.sleep(self._poll_interval_s)