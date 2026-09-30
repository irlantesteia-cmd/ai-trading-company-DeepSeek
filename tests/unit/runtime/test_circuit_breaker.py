import pytest

from app.core.exceptions import CircuitBreakerOpenError
from app.runtime.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_closed_calls_succeed():
    cb = CircuitBreaker(name="x", failure_threshold=3)

    async def ok():
        return 42

    assert await cb.call(ok) == 42
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_opens_after_failure_threshold():
    cb = CircuitBreaker(name="x", failure_threshold=3)

    async def boom():
        raise RuntimeError("net")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_rejects_without_calling_fn():
    cb = CircuitBreaker(name="x", failure_threshold=1)

    async def boom():
        raise RuntimeError("net")

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    called = False

    async def should_not_run():
        nonlocal called
        called = True
        return 1

    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(should_not_run)
    assert called is False


@pytest.mark.asyncio
async def test_half_open_transitions_after_timeout():
    now = [0.0]
    cb = CircuitBreaker(
        name="x",
        failure_threshold=1,
        recovery_timeout=10.0,
        success_threshold=2,
        clock=lambda: now[0],
    )

    async def boom():
        raise RuntimeError("net")

    with pytest.raises(RuntimeError):
        await cb.call(boom)
    assert cb.state is CircuitState.OPEN

    now[0] = 11.0

    async def ok():
        return "ok"

    await cb.call(ok)  # 1º sucesso em HALF_OPEN
    assert cb.state is CircuitState.HALF_OPEN
    await cb.call(ok)  # 2º sucesso → CLOSED
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_failure_reopens_immediately():
    now = [0.0]
    cb = CircuitBreaker(
        name="x",
        failure_threshold=1,
        recovery_timeout=1.0,
        success_threshold=2,
        clock=lambda: now[0],
    )

    async def boom():
        raise RuntimeError("net")

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    now[0] = 2.0

    async def ok():
        return 1

    await cb.call(ok)  # HALF_OPEN
    assert cb.state is CircuitState.HALF_OPEN

    with pytest.raises(RuntimeError):
        await cb.call(boom)
    assert cb.state is CircuitState.OPEN


def test_invalid_params():
    with pytest.raises(ValueError):
        CircuitBreaker(name="x", failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(name="x", recovery_timeout=0)
    with pytest.raises(ValueError):
        CircuitBreaker(name="x", success_threshold=0)