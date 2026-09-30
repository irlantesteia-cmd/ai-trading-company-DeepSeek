import pytest

from app.github.types import FileChange, ProposedChange, PullRequestResult
from app.runtime.evolution import EvolutionLoop


class _StubWorkflows:
    enabled: bool

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.calls: list[ProposedChange] = []

    async def propose_change(self, change: ProposedChange) -> PullRequestResult:
        self.calls.append(change)
        return PullRequestResult(
            number=len(self.calls),
            url=f"https://example/pr/{len(self.calls)}",
            title=change.title,
            head_branch=change.branch_name,
        )


def _change() -> ProposedChange:
    return ProposedChange(
        title="auto",
        body="b",
        branch_name="auto/x",
        files=[FileChange(path="app/strategies/x.py", content="new")],
    )


@pytest.mark.asyncio
async def test_tick_noop_when_disabled():
    wf = _StubWorkflows(enabled=False)
    calls = {"n": 0}

    async def metrics():
        calls["n"] += 1
        return {"win_rate": 0.9}

    async def gen(m):
        return _change()

    loop = EvolutionLoop(
        workflows=wf,  # type: ignore[arg-type]
        metrics_provider=metrics,
        change_generator=gen,
    )
    result = await loop.tick()
    assert result is None
    assert calls["n"] == 0  # nem chamou métricas


@pytest.mark.asyncio
async def test_tick_proposes_when_generator_returns_change():
    wf = _StubWorkflows()

    async def metrics():
        return {"win_rate": 0.2}

    async def gen(m):
        assert m == {"win_rate": 0.2}
        return _change()

    loop = EvolutionLoop(
        workflows=wf,  # type: ignore[arg-type]
        metrics_provider=metrics,
        change_generator=gen,
    )
    result = await loop.tick()
    assert result is not None
    assert loop.cycles == 1
    assert loop.proposals == 1
    assert len(wf.calls) == 1


@pytest.mark.asyncio
async def test_tick_noop_when_generator_returns_none():
    wf = _StubWorkflows()

    async def metrics():
        return {}

    async def gen(m):
        return None

    loop = EvolutionLoop(
        workflows=wf,  # type: ignore[arg-type]
        metrics_provider=metrics,
        change_generator=gen,
    )
    assert await loop.tick() is None
    assert loop.proposals == 0


@pytest.mark.asyncio
async def test_cooldown_prevents_burst():
    wf = _StubWorkflows()
    now = [1000.0]

    async def metrics():
        return {}

    async def gen(m):
        return _change()

    loop = EvolutionLoop(
        workflows=wf,  # type: ignore[arg-type]
        metrics_provider=metrics,
        change_generator=gen,
        cooldown_seconds=500.0,
        clock=lambda: now[0],
    )

    r1 = await loop.tick()
    assert r1 is not None

    # Ainda dentro do cooldown
    now[0] = 1200.0
    r2 = await loop.tick()
    assert r2 is None
    assert loop.proposals == 1

    # Passou do cooldown
    now[0] = 1600.0
    r3 = await loop.tick()
    assert r3 is not None
    assert loop.proposals == 2


def test_invalid_interval():
    wf = _StubWorkflows()
    with pytest.raises(ValueError):
        EvolutionLoop(
            workflows=wf,  # type: ignore[arg-type]
            metrics_provider=lambda: None,  # type: ignore[arg-type]
            change_generator=lambda m: None,  # type: ignore[arg-type]
            interval_seconds=0,
        )