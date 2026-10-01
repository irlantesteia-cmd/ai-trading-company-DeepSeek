import pytest

from app.events.bus import EventBus
from app.events.event import HealthCheckFailed
from app.monitoring.db_ping import ping_database_once, run_database_ping_loop


@pytest.mark.asyncio
async def test_ping_database_once_success_publishes_nothing():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe(HealthCheckFailed, handler)

    async def ok():
        return None

    ok_result = await ping_database_once(event_bus=bus, check_fn=ok)
    assert ok_result is True
    assert received == []


@pytest.mark.asyncio
async def test_ping_database_once_failure_publishes_event():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe(HealthCheckFailed, handler)

    async def boom():
        raise ConnectionRefusedError("postgres down")

    ok_result = await ping_database_once(event_bus=bus, check_fn=boom)
    assert ok_result is False
    assert len(received) == 1
    assert received[0].component == "database"
    assert "postgres down" in received[0].detail


@pytest.mark.asyncio
async def test_ping_database_once_handles_empty_exception_message():
    """Exceções sem mensagem (ex.: TimeoutError vazio) usam o tipo como fallback."""
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe(HealthCheckFailed, handler)

    async def boom():
        raise TimeoutError()  # sem mensagem

    await ping_database_once(event_bus=bus, check_fn=boom)
    assert received[0].detail == "TimeoutError"


@pytest.mark.asyncio
async def test_run_database_ping_loop_runs_cycles():
    bus = EventBus()
    calls = {"n": 0}

    async def counting_check():
        calls["n"] += 1

    loop_task = None
    try:
        import asyncio

        loop_task = asyncio.create_task(
            run_database_ping_loop(
                event_bus=bus, interval_seconds=0.01, check_fn=counting_check
            )
        )
        await asyncio.sleep(0.05)
    finally:
        if loop_task is not None:
            loop_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop_task

    # Em 50ms com intervalo de 10ms, esperamos >= 3 ciclos
    assert calls["n"] >= 3


def test_run_database_ping_loop_rejects_invalid_interval():
    import asyncio

    bus = EventBus()

    async def fake_check():
        return None

    async def _run():
        await run_database_ping_loop(
            event_bus=bus, interval_seconds=0, check_fn=fake_check
        )

    with pytest.raises(ValueError):
        asyncio.run(_run())