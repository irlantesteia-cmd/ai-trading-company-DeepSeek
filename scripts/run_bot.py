"""Entry point: sobe a AI Trading Company e roda até receber sinal de parada.

Uso:
    python scripts/run_bot.py

Requer .env com BINANCE_API_KEY / BINANCE_API_SECRET (ou TESTNET=true).
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
    TradingManager,
)
from app.core.config import settings
from app.core.enums import MarketType
from app.core.exceptions import ExchangeAuthError
from app.core.logging import setup_logging
from app.database.session import AsyncSessionLocal
from app.events.bus import EventBus
from app.events.event import HealthCheckFailed
from app.exchanges.binance.adapter import BinanceAdapter
from app.github.client import GitHubClient
from app.github.policies import default_policy
from app.github.workflows import GitHubWorkflows
from app.monitoring.health import HealthChecker
from app.orchestration.context import AgentContext
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.registry import AgentRegistry
from app.runtime.heartbeat import HeartbeatMonitor
from app.runtime.lifecycle import ApplicationLifecycle
from app.runtime.recovery import Reconciler

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging(settings.log_level)
    logger.info("boot.start", extra={"env": settings.app_env})

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

    # GitHub (opcional — só ativa se token+repo preenchidos)
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

    registry.register(TradingManager(context))
    registry.register(RiskAgent(context))
    registry.register(PortfolioAgent(context))
    registry.register(ExecutionAgent(context))
    registry.register(AuditorAgent(context))
    registry.register(QAAgent(context))
    registry.register(EngineeringAgent(context, heartbeat=heartbeat, workflows=workflows))
    registry.register(ResearchAgent(context, workflows=workflows))
    registry.register(MLAgent(context, model_dir=Path("models")))

    market_type = MarketType(settings.default_market_type)
    for symbol in settings.trading_symbols:
        registry.register(
            AssetAgent(
                context,
                symbol=symbol,
                market_type=market_type,
                interval=settings.default_interval,
            )
        )

    orchestrator = Orchestrator(context, registry)

    health = HealthChecker()
    health.register("exchange.ping", exchange.ping)

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
                # Credenciais ausentes/inválidas — pular ciclo sem tratar como falha sistêmica.
                logger.warning(
                    "reconcile.skipped",
                    extra={"reason": "auth", "detail": str(exc)},
                )
            except Exception:
                logger.exception("reconcile.failed")
            await asyncio.sleep(settings.reconcile_interval_s)

    tasks: list = [heartbeat_task, health_task]
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