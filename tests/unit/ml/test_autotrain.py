from dataclasses import dataclass

import pytest

from app.core.enums import MarketType
from app.ml.autotrain import AutoTrainSummary, autotrain_all


@dataclass
class _Metadata:
    version: str
    n_train: int
    n_test: int


@dataclass
class _TrainingResult:
    metadata: _Metadata
    test_accuracy: float


class _FakeMLAgent:
    def __init__(self, *, failing: set[str] | None = None) -> None:
        self._failing = failing or set()
        self.calls: list[str] = []

    async def train(self, *, symbol, market_type, interval, limit):
        self.calls.append(symbol)
        if symbol in self._failing:
            raise RuntimeError(f"no data for {symbol}")
        return _TrainingResult(
            metadata=_Metadata(
                version=f"{symbol}_h5_20250101T000000Z",
                n_train=400,
                n_test=100,
            ),
            test_accuracy=0.62,
        )


@pytest.mark.asyncio
async def test_all_symbols_succeed():
    agent = _FakeMLAgent()
    summary = await autotrain_all(
        ml_agent=agent,
        symbols=["BTCUSDT", "ETHUSDT"],
        market_type=MarketType.FUTURES,
        interval="5m",
        limit=500,
    )
    assert isinstance(summary, AutoTrainSummary)
    assert summary.total == 2
    assert summary.succeeded == 2
    assert summary.failed == 0
    assert agent.calls == ["BTCUSDT", "ETHUSDT"]
    assert all(r.success for r in summary.reports)
    assert summary.reports[0].version == "BTCUSDT_h5_20250101T000000Z"
    assert summary.reports[0].n_train == 400
    assert summary.reports[0].test_accuracy == pytest.approx(0.62)


@pytest.mark.asyncio
async def test_partial_failure_isolated():
    agent = _FakeMLAgent(failing={"ETHUSDT"})
    summary = await autotrain_all(
        ml_agent=agent,
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    assert summary.total == 3
    assert summary.succeeded == 2
    assert summary.failed == 1
    eth_report = next(r for r in summary.reports if r.symbol == "ETHUSDT")
    assert eth_report.success is False
    assert eth_report.error is not None
    assert "no data" in eth_report.error
    assert eth_report.version is None


@pytest.mark.asyncio
async def test_all_failures_do_not_raise():
    agent = _FakeMLAgent(failing={"BTCUSDT", "ETHUSDT"})
    summary = await autotrain_all(
        ml_agent=agent,
        symbols=["BTCUSDT", "ETHUSDT"],
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    assert summary.succeeded == 0
    assert summary.failed == 2


@pytest.mark.asyncio
async def test_empty_symbols():
    agent = _FakeMLAgent()
    summary = await autotrain_all(
        ml_agent=agent,
        symbols=[],
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    assert summary.total == 0
    assert summary.succeeded == 0
    assert summary.failed == 0
    assert agent.calls == []


@pytest.mark.asyncio
async def test_summary_reports_are_ordered():
    agent = _FakeMLAgent(failing={"BTCUSDT"})
    summary = await autotrain_all(
        ml_agent=agent,
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    assert [r.symbol for r in summary.reports] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]