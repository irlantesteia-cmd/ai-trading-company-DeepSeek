import pytest

from app.agents.research import ResearchAgent
from app.agents.risk import RiskAgent
from app.core.enums import AgentRole
from app.core.exceptions import ConfigurationError
from app.orchestration.registry import AgentRegistry


def test_register_and_get(context):
    registry = AgentRegistry()
    agent = RiskAgent(context)
    registry.register(agent)

    assert registry.get("risk_officer") is agent
    assert registry.names() == ["risk_officer"]


def test_register_duplicate_raises(context):
    registry = AgentRegistry()
    registry.register(RiskAgent(context))
    with pytest.raises(ConfigurationError):
        registry.register(RiskAgent(context))


def test_get_missing_raises(context):
    registry = AgentRegistry()
    with pytest.raises(ConfigurationError):
        registry.get("nope")


def test_by_role(context):
    registry = AgentRegistry()
    registry.register(RiskAgent(context))
    registry.register(ResearchAgent(context))

    assert len(registry.by_role(AgentRole.RISK)) == 1
    assert len(registry.by_role(AgentRole.RESEARCH)) == 1
    assert registry.by_role(AgentRole.ML) == []


def test_require_one(context):
    registry = AgentRegistry()
    registry.register(RiskAgent(context))
    assert registry.require_one(AgentRole.RISK).name == "risk_officer"

    with pytest.raises(ConfigurationError):
        registry.require_one(AgentRole.ML)


@pytest.mark.asyncio
async def test_start_stop_all(context):
    registry = AgentRegistry()
    registry.register(RiskAgent(context))
    registry.register(ResearchAgent(context))

    await registry.start_all()
    assert all(a.started for a in registry.all())

    await registry.stop_all()
    assert all(not a.started for a in registry.all())