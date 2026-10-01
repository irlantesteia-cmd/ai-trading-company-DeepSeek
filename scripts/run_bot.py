"""Entry point: sobe a AI Trading Company e roda até receber sinal de parada.

Uso:
    python scripts/run_bot.py

Requer .env com BINANCE_API_KEY / BINANCE_API_SECRET (ou TESTNET=true).
Opcionalmente GITHUB_TOKEN + GITHUB_REPO para habilitar auto-evolução.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.agents import (
    AssetAgent,
    AuditorAgent,
    EngineeringAgent,
    ExecutionAgent,
    MLAgent,
    PortfolioAgent,
    QAAgent,
    ResearchAgent,
    RiskAgent,
    TradeRecorderAgent,
    TradingManager,
)
from app.core.config import settings
from app.core.enums import MarketType
from app.core.exceptions import ConfigurationError, ExchangeAuthError
from app.core.logging import setup_logging
from app.database.session import AsyncSessionLocal, check_connection
from app.events.bus import EventBus
from app.events.event import HealthCheckFailed
from app.exchanges.binance.adapter import BinanceAdapter
from app.github.client import GitHubClient
from app.github.policies import default_policy
from app.github.workflows import GitHubWorkflows
from app.market.backfill import HistoryBackfillService
from app.market.candle_stream import CandleStreamService
from app.ml.autotrain import autotrain_all
from app.monitoring.db_ping import run_database_ping_loop
from app.monitoring.health import HealthChecker
from app.orchestration.context import AgentContext
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.registry import AgentRegistry
from app.runtime.evolution import EvolutionLoop, NullChangeGenerator
from app.runtime.heartbeat import HeartbeatMonitor
from app.runtime.lifecycle import ApplicationLifecycle
from app.runtime.metrics_collector import MetricsCollector
from app.runtime.recovery import Reconciler
from app.strategies import Strategy, make_strategy

logger = logging.getLogger(__name__)

MODEL_DIR = Path("models")


def _resolve_strategies() -> dict[str, Strategy | None]:
    """Resolve a estratégia de cada símbolo. Falha rápido se spec inválida."""
    resolved: dict[str, Strategy | None] = {}
    for symbol in settings.trading_symbols:
        spec = settings.strategy_per_symbol.get(symbol, settings.default_strategy)
        try:
            resolved[symbol] = make_strategy(
                spec, symbol=symbol, model_dir=MODEL_DIR, horizon=settings.ml_horizon
            )
        except ConfigurationError as exc:
            logger.error(
                "boot.invalid_strategy_spec",
                extra={"symbol": symbol, "spec": spec, "error": str(exc)},
            )
            raise SystemExit(1) from None
    return resolved


async def main() -> None:
    setup_logging(settings.log_level)
    logger.info("boot.start", extra={"env": settings.app_env})

    try:
        await check_connection()
        logger.info("boot.db_ok")
    except Exception:
        logger.exception("boot.db_unreachable")
        raise SystemExit(1) from None

    strategies = _resolve_strategies()
    logger.info(
        "boot.strategies_resolved",
        extra={
            "symbols": settings.trading_symbols,
            "per_symbol": {
                s: settings.strategy_per_symbol.get(s, settings.default_strategy)
                for s in settings.trading_symbols
            },
        },
    )

    exchange = BinanceAdapter(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=settings.binance_testnet,
    )

    event_bus = EventBus()
    registry = AgentRegistry()
    context = AgentContext(
        exchange=exchange,
        event_bus=event_bus,
        settings=settings,
        registry=registry,
        session_factory=AsyncSessionLocal,
    )

    workflows: GitHubWorkflows | None = None
    gh_client: GitHubClient | None = None
    if settings.github_token and settings.github_repo:
        gh_client = GitHubClient(
            token=settings.github_token,
            repo=settings.github_repo,
            base_url=settings.github_api_url,
        )
        workflows = GitHubWorkflows(
            client=gh_client,
            policy=default_policy(),
            enabled=settings.github_autonomy_enabled,
        )

    heartbeat = HeartbeatMonitor(max_age_seconds=settings.heartbeat_max_age_s)

    ml_agent = MLAgent(context, model_dir=MODEL_DIR, horizon=settings.ml_horizon)

    registry.register(TradingManager(context))
    registry.register(RiskAgent(context))
    registry.register(PortfolioAgent(context))
    registry.register(ExecutionAgent(context))
    registry.register(AuditorAgent(context))
    registry.register(TradeRecorderAgent(context))
    registry.register(QAAgent(context))
    registry.register(
        EngineeringAgent(context, heartbeat=heartbeat, workflows=workflows)
    )
    registry.register(ResearchAgent(context, workflows=workflows))
    registry.register(ml_agent)

    market_type = MarketType(settings.default_market_type)
    for symbol in settings.trading_symbols:
        registry.register(
            AssetAgent(
                context,
                symbol=symbol,
                market_type=market_type,
                interval=settings.default_interval,
                strategy=strategies.get(symbol),
            )
        )

    orchestrator = Orchestrator(context, registry)

    health = HealthChecker()
    health.register("exchange.ping", exchange.ping)

    backfill = HistoryBackfillService(
        exchange=exchange,
        session_factory=AsyncSessionLocal,
    )

    backfill_first_run_done = asyncio.Event()

    async def db_ping_task() -> None:
        await run_database_ping_loop(
            event_bus=event_bus,
            interval_seconds=settings.db_ping_interval_s,
        )

    async def heartbeat_task() -> None:
        while True:
            for agent in registry.all():
                if agent.started:
                    heartbeat.beat(agent.name)
            await asyncio.sleep(settings.heartbeat_interval_s)

    async def health_task() -> None:
        while True:
            report = await health.run()
            for c in report.components:
                if c.status == "down":
                    await event_bus.publish(
                        HealthCheckFailed(component=c.name, detail=c.detail)
                    )
            await asyncio.sleep(settings.health_check_interval_s)

    async def reconcile_task() -> None:
        reconciler = Reconciler(
            exchange=exchange,
            session_factory=AsyncSessionLocal,
        )
        while True:
            try:
                report = await reconciler.reconcile(market_type=market_type)
                if not report.balanced:
                    await event_bus.publish(
                        HealthCheckFailed(
                            component="reconciliation",
                            detail=(
                                f"missing_on_exchange={report.missing_on_exchange} "
                                f"missing_in_db={report.missing_in_db}"
                            ),
                        )
                    )
            except ExchangeAuthError as exc:
                logger.warning(
                    "reconcile.skipped",
                    extra={"reason": "auth", "detail": str(exc)},
                )
            except Exception:
                logger.exception("reconcile.failed")
            await asyncio.sleep(settings.reconcile_interval_s)

    async def backfill_task() -> None:
        async def _run_once() -> None:
            try:
                await backfill.backfill(
                    symbols=list(settings.trading_symbols),
                    interval=settings.default_interval,
                    market_type=market_type,
                    limit=500,
                )
            except Exception:
                logger.exception("backfill.failed")

        await _run_once()
        backfill_first_run_done.set()

        while True:
            await asyncio.sleep(86400)
            await _run_once()

    async def candle_stream_task() -> None:
        streams = [
            CandleStreamService(
                exchange=exchange,
                event_bus=event_bus,
                symbol=symbol,
                interval=settings.default_interval,
                market_type=market_type,
                poll_interval_s=settings.candle_stream_interval_s,
            )
            for symbol in settings.trading_symbols
        ]
        await asyncio.gather(*(s.run_forever() for s in streams))

    async def autotrain_task() -> None:
        await backfill_first_run_done.wait()

        symbols = settings.ml_autotrain_symbols or list(settings.trading_symbols)
        if not symbols:
            logger.info("ml.autotrain_no_symbols")
            return

        logger.info(
            "ml.autotrain_starting",
            extra={"symbols": symbols, "limit": settings.ml_autotrain_limit},
        )
        try:
            await autotrain_all(
                ml_agent=ml_agent,
                symbols=symbols,
                market_type=market_type,
                interval=settings.default_interval,
                limit=settings.ml_autotrain_limit,
            )
        except Exception:
            logger.exception("ml.autotrain_failed")

    async def evolution_task() -> None:
        collector = MetricsCollector(
            session_factory=AsyncSessionLocal,
            lookback_hours=24,
        )

        async def metrics_provider() -> dict[str, float]:
            return (await collector.collect()).as_dict()

        loop = EvolutionLoop(
            workflows=workflows,  # type: ignore[arg-type]
            metrics_provider=metrics_provider,
            change_generator=NullChangeGenerator(),
            interval_seconds=settings.evolution_interval_s,
            cooldown_seconds=settings.evolution_cooldown_s,
        )
        await loop.run_forever()

    tasks: list = [db_ping_task, heartbeat_task, health_task, backfill_task]

    if settings.candle_stream_enabled:
        tasks.append(candle_stream_task)
        logger.info(
            "candle_stream.enabled",
            extra={
                "symbols": list(settings.trading_symbols),
                "interval": settings.default_interval,
                "poll_interval_s": settings.candle_stream_interval_s,
            },
        )
    else:
        logger.info("candle_stream.disabled")

    if settings.ml_autotrain_on_boot:
        tasks.append(autotrain_task)
        logger.info(
            "ml.autotrain_enabled",
            extra={
                "symbols": settings.ml_autotrain_symbols
                or list(settings.trading_symbols),
                "limit": settings.ml_autotrain_limit,
            },
        )
    else:
        logger.info("ml.autotrain_disabled")

    if settings.binance_api_key and settings.binance_api_secret:
        tasks.append(reconcile_task)
    else:
        logger.warning(
            "reconcile.disabled",
            extra={
                "reason": "BINANCE_API_KEY/SECRET ausentes no .env",
                "hint": "preencha .env para ativar a reconciliação DB <-> exchange",
            },
        )

    if workflows is not None:
        tasks.append(evolution_task)
        if workflows.enabled:
            logger.info(
                "evolution.loop_enabled",
                extra={
                    "interval_s": settings.evolution_interval_s,
                    "cooldown_s": settings.evolution_cooldown_s,
                },
            )
        else:
            logger.info(
                "evolution.loop_in_observation_mode",
                extra={
                    "reason": "GITHUB_AUTONOMY_ENABLED=false",
                    "hint": "defina como true no .env para permitir PRs automáticos",
                },
            )
    else:
        logger.warning(
            "evolution.disabled",
            extra={
                "reason": "GITHUB_TOKEN/GITHUB_REPO ausentes no .env",
                "hint": "preencha .env para ativar o loop de evolução",
            },
        )

    lifecycle = ApplicationLifecycle(orchestrator=orchestrator, tasks=tasks)

    try:
        await lifecycle.run_forever()
    finally:
        if gh_client is not None:
            await gh_client.close()
        await exchange.close()
        logger.info("boot.stop")


def _run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("boot.interrupted")


if __name__ == "__main__":
    _run()