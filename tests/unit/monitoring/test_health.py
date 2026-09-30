import pytest

from app.monitoring.health import HealthChecker


@pytest.mark.asyncio
async def test_all_ok():
    hc = HealthChecker()

    async def ok():
        return "ok"

    hc.register("exchange", ok)
    hc.register("db", ok)
    report = await hc.run()
    assert report.status == "ok"
    assert len(report.components) == 2
    assert hc.names() == ["exchange", "db"]


@pytest.mark.asyncio
async def test_down_when_any_fails():
    hc = HealthChecker()

    async def ok():
        return "ok"

    async def boom():
        raise RuntimeError("no route")

    hc.register("exchange", ok)
    hc.register("db", boom)
    report = await hc.run()
    assert report.status == "down"
    db = next(c for c in report.components if c.name == "db")
    assert db.status == "down"
    assert "no route" in db.detail


@pytest.mark.asyncio
async def test_report_as_dict():
    hc = HealthChecker()

    async def ok():
        return "fine"

    hc.register("x", ok)
    report = await hc.run()
    d = report.as_dict()
    assert d["status"] == "ok"
    assert d["components"][0]["name"] == "x"