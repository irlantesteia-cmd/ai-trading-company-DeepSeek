"""Serviço do modo sombra (Fase 6b): um ciclo por vela de 4h.

A cada ciclo: lê a hipótese no registro (`docs/research/hypotheses.json`),
carrega velas e funding públicos da Binance, refaz a simulação de cada
ativo × intervalo desde o T0 (`run_shadow`) e grava os trades hipotéticos e
um resumo do ciclo. Sem hipótese registrada com `shadow_start`, só grava o
ciclo como NOT_REGISTERED: o teste não começa sem pré-registro.

Nunca envia ordens nem usa chaves: só dados públicos.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.backtest.data import load_candles, load_funding
from app.backtest.registry import load_declared
from app.backtest.shadow import CLOSED, OPEN, run_shadow, shadow_rows, spec_from_registry
from app.database.models.shadow_run import ShadowRunORM
from app.database.repositories.shadow_trade import ShadowTradeRepository
from app.domain.models.market import Candle
from app.market.validation import interval_ms

logger = logging.getLogger(__name__)

CandleLoader = Callable[[str, str, float], Awaitable[list[Candle]]]
FundingLoader = Callable[[str, float], Awaitable[list[tuple[int, float]]]]

REGISTERED = "REGISTERED"
NOT_REGISTERED = "NOT_REGISTERED"
INVALID = "INVALID"
# Fluxo atrasado: a última vela fechada tem mais de 2 intervalos.
_STALE_INTERVALS = 2


class ShadowCycle(BaseModel):
    hypothesis: str
    ran_at: datetime
    status: str
    start: datetime | None = None
    last_candle_close: datetime | None = None
    streams: int = 0
    streams_failed: int = 0
    streams_stale: int = 0
    streams_short_warmup: int = 0
    closed_trades: int = 0
    open_positions: int = 0
    error: str | None = None


class ShadowStore(Protocol):
    async def save_trades(self, rows: list[dict[str, Any]]) -> None: ...

    async def record_cycle(self, cycle: ShadowCycle) -> None: ...


async def default_loader(symbol: str, interval: str, days: float) -> list[Candle]:
    return await load_candles(symbol, interval, days=days)


async def default_funding_loader(symbol: str, days: float) -> list[tuple[int, float]]:
    return await load_funding(symbol, days=days)


class ShadowRunner:
    def __init__(
        self,
        *,
        hypothesis: str,
        registry_path: Path,
        store: ShadowStore,
        loader: CandleLoader | None = None,
        funding_loader: FundingLoader | None = None,
    ) -> None:
        self.hypothesis = hypothesis
        self.registry_path = registry_path
        self._store = store
        self._load = loader or default_loader
        self._load_funding = funding_loader or default_funding_loader

    async def run_once(self, now: datetime | None = None) -> ShadowCycle:
        now = now or datetime.now(UTC)
        cycle = await self._run(now)
        await self._store.record_cycle(cycle)
        logger.info("shadow.cycle", extra=cycle.model_dump(mode="json"))
        return cycle

    async def _run(self, now: datetime) -> ShadowCycle:
        try:
            spec = spec_from_registry(self.hypothesis, load_declared(self.registry_path))
        except (OSError, ValueError, TypeError) as exc:  # registro ilegível ou inválido: não roda
            logger.error("shadow.invalid_registry", extra={"error": str(exc)})
            return ShadowCycle(hypothesis=self.hypothesis, ran_at=now, status=INVALID, error=str(exc))
        if spec is None:
            logger.warning("shadow.not_registered", extra={"hypothesis": self.hypothesis})
            return ShadowCycle(hypothesis=self.hypothesis, ran_at=now, status=NOT_REGISTERED)

        cycle = ShadowCycle(hypothesis=self.hypothesis, ran_at=now, status=REGISTERED, start=spec.start)
        funding_days = max(spec.history_days(i, now) for i in spec.lab.intervals)
        funding: dict[str, list[tuple[int, float]]] = {}
        errors: list[str] = []
        for interval in spec.lab.intervals:
            step = timedelta(milliseconds=interval_ms(interval) or 0)
            for symbol in spec.lab.symbols:
                cycle.streams += 1
                try:
                    candles = await self._load(symbol, interval, spec.history_days(interval, now))
                    rates = None
                    if spec.lab.funding:
                        if symbol not in funding:
                            funding[symbol] = await self._load_funding(symbol, funding_days)
                        rates = funding[symbol]
                    run = run_shadow(spec, symbol=symbol, interval=interval, candles=candles, funding=rates)
                    rows = shadow_rows(spec, run)
                    await self._store.save_trades(rows)
                except Exception as exc:
                    logger.exception("shadow.stream_failed", extra={"symbol": symbol, "interval": interval})
                    cycle.streams_failed += 1
                    errors.append(f"{symbol} {interval}: {exc}")
                    continue
                cycle.closed_trades += sum(r["status"] == CLOSED for r in rows)
                cycle.open_positions += sum(r["status"] == OPEN for r in rows)
                if run.last_close_time is None or now - run.last_close_time > _STALE_INTERVALS * step:
                    cycle.streams_stale += 1
                if run.warmup_candles < spec.warmup_bars:
                    cycle.streams_short_warmup += 1
                if run.last_close_time is not None and (
                    cycle.last_candle_close is None or run.last_close_time > cycle.last_candle_close
                ):
                    cycle.last_candle_close = run.last_close_time
        if errors:
            cycle.error = "; ".join(errors)[:2000]
        return cycle


class DbShadowStore:
    """Grava no Postgres: trades por upsert e um registro por ciclo."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def save_trades(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        async with self._session_factory() as session:
            await ShadowTradeRepository(session).bulk_upsert(rows)
            await session.commit()

    async def record_cycle(self, cycle: ShadowCycle) -> None:
        async with self._session_factory() as session:
            session.add(ShadowRunORM(**cycle.model_dump()))
            await session.commit()
