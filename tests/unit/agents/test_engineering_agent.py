import pytest

from app.agents.engineering import EngineeringAgent
from app.events.event import HealthCheckFailed, RiskLimitBreached


@pytest.mark.asyncio
async def test_engineering_counts_health_failures(context):
    agent = EngineeringAgent(context)
    await agent.start()
    await context.event_bus.publish(
        HealthCheckFailed(component="exchange", detail="no route")
    )
    await context.event_bus.publish(
        HealthCheckFailed(component="db", detail="timeout")
    )
    assert agent.failures_seen == 2


@pytest.mark.asyncio
async def test_engineering_handles_risk_breach(context):
    agent = EngineeringAgent(context)
    await agent.start()
    # Não deve explodir
    await context.event_bus.publish(
        RiskLimitBreached(rule="daily_loss", detail="limit hit")
    )
    assert agent.failures_seen == 0


@pytest.mark.asyncio
async def test_engineering_resets_heartbeat_on_stop(context):
    agent = EngineeringAgent(context)
    agent.heartbeat.beat("x")
    await agent.start()
    await agent.stop()
    assert agent.heartbeat.known() == []