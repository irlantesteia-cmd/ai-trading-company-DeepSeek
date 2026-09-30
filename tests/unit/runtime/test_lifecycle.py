import asyncio

import pytest

from app.agents.risk import RiskAgent
from app.orchestration.orchestrator import Orchestrator
from app.runtime.lifecycle import ApplicationLifecycle


def _build(context) -> Orchestrator:
    registry = context.registry
    registry.register(RiskAgent(context))
    return Orchestrator(context=context, registry=registry)


@pytest.mark.asyncio
async def test_start_and_stop(context):
    orch = _build(context)
    lc = ApplicationLifecycle(orchestrator=orch, install_signal_handlers=False)
    await lc.start()
    assert lc.started is True
    assert orch.running is True
    await lc.stop()
    assert lc.started is False
    assert orch.running is False


@pytest.mark.asyncio
async def test_shutdown_request(context):
    orch = _build(context)
    lc = ApplicationLifecycle(orchestrator=orch, install_signal_handlers=False)
    await lc.start()
    assert lc.shutdown_requested is False
    lc.request_shutdown()
    assert lc.shutdown_requested is True
    await lc.stop()


@pytest.mark.asyncio
async def test_background_tasks_started_and_cancelled(context):
    orch = _build(context)
    ticks: list[int] = []

    async def bg():
        while True:
            ticks.append(1)
            await asyncio.sleep(0.01)

    lc = ApplicationLifecycle(
        orchestrator=orch,
        tasks=[bg],
        install_signal_handlers=False,
    )
    await lc.start()
    await asyncio.sleep(0.05)
    await lc.stop()
    assert len(ticks) >= 1


@pytest.mark.asyncio
async def test_run_forever_stops_on_request(context):
    orch = _build(context)
    lc = ApplicationLifecycle(orchestrator=orch, install_signal_handlers=False)

    async def trigger():
        await asyncio.sleep(0.02)
        lc.request_shutdown()

    await asyncio.gather(lc.run_forever(), trigger())
    assert lc.started is False