"""Entry point: sobe a AI Trading Company e roda até receber sinal de parada."""

from __future__ import annotations

import asyncio
import json
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
    RoundTripAgent,
    SignalRecorderAgent,
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
from app.exchanges.binance.client import BinanceClient
from app.exchanges.binance.user_stream import UserDataStreamClient
from app.exchanges.symbol_info import SymbolInfoService
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
from app.runtime.boot_cleanup import (
    cancel_orphan_conditional_orders_on_boot,
    close_all_futures_positions_on_boot,
)
from app.runtime.change_generator import HeuristicChangeGenerator
from app.runtime.evolution import EvolutionLoop
from app.runtime.heartbeat import HeartbeatMonitor
from app.runtime.lifecycle import ApplicationLifecycle
from app.runtime.metrics_collector import MetricsCollector
from app.runtime.order_recorder import OrderRecorder, RecordingOrderProvider
from app.runtime.recovery import Reconciler
from app.runtime.time_exit import TimeExitMonitor
from app.runtime.user_stream_handler import handle_user_stream_event
from app.strategies import Strategy, make_strategy

logger = logging.getLogger(__name__)
MODEL_DIR = Path("models")
EVOLUTION_CONFIG_PATH = Path("config/evolution/thresholds.json")


def _load_threshold_overrides() -> dict[str, float] | None:
    """Lê overrides de threshold ML do arquivo gerenciado pelo EvolutionLoop.

    Retorna `None` se o arquivo não existir ou estiver inválido — nesse caso
    `_resolve_strategies` cai para os defaults de `settings`.

    Lê com `utf-8-sig` para tolerar BOM (arquivos editados em Windows via
    `Set-Content -Encoding UTF8` no PowerShell 5.1 ganham BOM).
    """
    if not EVOLUTION_CONFIG_PATH.exists():
        return None
    try:
        raw = json.loads(EVOLUTION_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        logger.exception("boot.evolution_config_read_failed")
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return {
            "long_threshold": float(raw["long_threshold"]),
            "short_threshold": float(raw["short_threshold"]),
        }
    except (KeyError, TypeError, ValueError):
        logger.warning("boot.evolution_config_invalid", extra={"raw": raw})
        return None


def _resolve_strategies() -> dict[str, Strategy | None]:
    overrides = _load_threshold_overrides()
    long_t = (
        overrides["long_threshold"]
        if overrides is not None
        else settings.ml_long_threshold
    )
    short_t = (
        overrides["short_threshold"]
        if overrides is not None
        else settings.ml_short_threshold
    )
    if overrides is not None:
        logger.info(
            "boot.evolution_config_applied",
            extra={
                "long_threshold": long_t,
                "short_threshold": short_t,
                "source": str(EVOLUTION_CONFIG_PATH),
            },
        )

    resolved: dict[str, Strategy | None] = {}
    for symbol in settings.trading_symbols:
        spec = settings.strategy_per_symbol.get(symbol, settings.default_strategy)
        # Cross-asset: só faz sentido para spec "ml" com ref configurado.
        ref_symbol = settings.ml_cross_asset_ref.get(symbol)
        try:
            resolved[symbol] = make_strategy(
                spec,
                symbol=symbol,
                model_dir=MODEL_DIR,
                horizon=settings.ml_horizon,
                ml_long_threshold=long_t,
                ml_short_threshold=short_t,
                ref_symbol=ref_symbol,
                ref_horizons=settings.ml_cross_asset_ref_horizons,
            )
        except ConfigurationError as exc:
            logger.error(
                "boot.invalid_strategy_spec",
                extra={"symbol": symbol, "spec": spec, "error": str(exc)},
            )
            raise SystemExit(1) from None
    return resolved


def _extract_binance_client(exchange: BinanceAdapter) -> BinanceClient:
    """Extrai o `BinanceClient` subjacente do `BinanceAdapter`.

    O adapter pode expor como `client` (público) ou `_client` (privado).
    """
    for attr in ("client", "_client"):
        obj = getattr(exchange, attr, None)
        if isinstance(obj, BinanceClient):
            return obj
    raise RuntimeError(
        "BinanceAdapter não expõe o BinanceClient subjacente. "
        "Adicione uma propriedade `client` em app/exchanges/binance/adapter.py."
    )


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
            "cross_asset_refs": dict(settings.ml_cross_asset_ref),
            "cross_asset_horizons": list(settings.ml_cross_asset_ref_horizons),
        },
    )

    # Toda ordem (entrada, SL/TP, fechamento) passa pelo recorder → `orders`.
    order_recorder = OrderRecorder(AsyncSessionLocal)
    exchange = BinanceAdapter(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=settings.binance_testnet,
        orders_wrapper=lambda inner: RecordingOrderProvider(inner, order_recorder),
    )

    market_type = MarketType(settings.default_market_type)
    symbol_info = SymbolInfoService()
    loaded = await symbol_info.load(exchange, market_type=market_type)
    if loaded == 0:
        logger.warning("boot.symbol_info_empty")

    if settings.close_positions_on_boot:
        if settings.binance_api_key and settings.binance_api_secret:
            await close_all_futures_positions_on_boot(exchange)
        else:
            logger.warning("boot_cleanup.skipped_no_credentials")
    else:
        logger.info("boot_cleanup.disabled")

    # Órfãs (SL/TP sem posição) são canceladas sempre, mesmo sem fechar
    # posições no boot. A reconciliação em seguida grava o CANCELED em `orders`.
    if settings.binance_api_key and settings.binance_api_secret:
        orphans = await cancel_orphan_conditional_orders_on_boot(exchange)
        if orphans:
            try:
                await Reconciler(
                    exchange=exchange,
                    session_factory=AsyncSessionLocal,
                    recorder=order_recorder,
                ).reconcile(market_type=market_type)
            except Exception:
                # O reconcile_task periódico corrige depois; o boot segue.
                logger.exception("boot_cleanup.reconcile_failed")

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
    ml_agent = MLAgent(
        context,
        model_dir=MODEL_DIR,
        horizon=settings.ml_horizon,
        cross_asset_refs=settings.ml_cross_asset_ref,
        cross_asset_horizons=settings.ml_cross_asset_ref_horizons,
    )

    registry.register(TradingManager(context))
    registry.register(RiskAgent(context))
    registry.register(PortfolioAgent(context))
    registry.register(ExecutionAgent(context, symbol_info=symbol_info))
    registry.register(AuditorAgent(context))
    registry.register(RoundTripAgent(context))
    registry.register(TradeRecorderAgent(context))
    registry.register(SignalRecorderAgent(context))
    registry.register(QAAgent(context))
    registry.register(
        EngineeringAgent(context, heartbeat=heartbeat, workflows=workflows)
    )
    registry.register(ResearchAgent(context, workflows=workflows))
    registry.register(ml_agent)

    for symbol in settings.trading_symbols:
        registry.register(
            AssetAgent(
                context,
                symbol=symbol,
                market_type=market_type,
                interval=settings.default_interval,
                strategy=strategies.get(symbol),
                ref_symbol=settings.ml_cross_asset_ref.get(symbol),
            )
        )

    orchestrator = Orchestrator(context, registry)
    health = HealthChecker()
    health.register("exchange.ping", exchange.ping)
    backfill = HistoryBackfillService(
        exchange=exchange, session_factory=AsyncSessionLocal
    )
    backfill_first_run_done = asyncio.Event()

    async def db_ping_task() -> None:
        await run_database_ping_loop(
            event_bus=event_bus, interval_seconds=settings.db_ping_interval_s
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
            recorder=order_recorder,
            record_runs=True,
        )
        while True:
            try:
                report = await reconciler.reconcile(market_type=market_type)
                if not report.balanced:
                    await event_bus.publish(
                        HealthCheckFailed(
                            component="reconciliation",
                            detail=f"unresolved={report.unresolved}",
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
            extra={
                "symbols": symbols,
                "cross_asset_refs": {
                    s: settings.ml_cross_asset_ref.get(s) for s in symbols
                },
            },
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
        if workflows is None:
            logger.warning("evolution.no_workflows_configured")
            return

        collector = MetricsCollector(
            session_factory=AsyncSessionLocal, lookback_hours=24
        )

        async def metrics_provider() -> dict[str, float]:
            return (await collector.collect()).as_dict()

        change_generator = HeuristicChangeGenerator(
            config_path=EVOLUTION_CONFIG_PATH,
            repo_path="config/evolution/thresholds.json",
        )
        loop = EvolutionLoop(
            workflows=workflows,
            metrics_provider=metrics_provider,
            change_generator=change_generator,
            interval_seconds=settings.evolution_interval_s,
            cooldown_seconds=settings.evolution_cooldown_s,
        )
        await loop.run_forever()

    async def user_stream_task() -> None:
        if not (settings.binance_api_key and settings.binance_api_secret):
            logger.warning("user_stream.skipped_no_credentials")
            return
        try:
            client = _extract_binance_client(exchange)
        except RuntimeError:
            logger.exception("user_stream.no_client")
            return
        uds = UserDataStreamClient(client)
        try:
            async for event in uds.stream():
                try:
                    await handle_user_stream_event(
                        event,
                        market_type=market_type,
                        event_bus=event_bus,
                        recorder=order_recorder,
                    )
                except Exception:
                    logger.exception("user_stream.event_handler_failed")
        finally:
            await uds.stop()

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
        logger.info("reconcile.enabled")
    else:
        logger.warning("reconcile.disabled")

    if settings.position_max_holding_minutes > 0 and settings.binance_api_key:
        time_exit = TimeExitMonitor(
            exchange=exchange,
            event_bus=event_bus,
            session_factory=AsyncSessionLocal,
            max_holding_minutes=settings.position_max_holding_minutes,
            check_interval_s=settings.time_exit_check_interval_s,
        )
        tasks.append(time_exit.run_forever)
        logger.info(
            "time_exit.enabled",
            extra={"max_holding_minutes": settings.position_max_holding_minutes},
        )
    else:
        logger.info("time_exit.disabled")

    if settings.user_stream_enabled:
        tasks.append(user_stream_task)
        logger.info("user_stream.enabled")
    else:
        logger.info("user_stream.disabled")

    if workflows is not None:
        tasks.append(evolution_task)
        if workflows.enabled:
            logger.info("evolution.loop_enabled")
        else:
            logger.info("evolution.loop_in_observation_mode")
    else:
        logger.warning("evolution.disabled")

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