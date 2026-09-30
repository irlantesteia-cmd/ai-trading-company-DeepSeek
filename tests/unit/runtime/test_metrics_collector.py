from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.runtime.metrics_collector import MetricsCollector


class _Row:
    def __init__(self, *, fee: str = "0", realized_pnl: str | None = None) -> None:
        self.fee = Decimal(fee)
        self.realized_pnl = Decimal(realized_pnl) if realized_pnl is not None else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


def _factory(rows):
    def _make():
        return _FakeSession(rows)

    return _make


def test_rejects_invalid_lookback():
    with pytest.raises(ValueError):
        MetricsCollector(session_factory=_factory([]), lookback_hours=0)


@pytest.mark.asyncio
async def test_empty_db_yields_zeroed_metrics():
    collector = MetricsCollector(session_factory=_factory([]), lookback_hours=24)
    m = await collector.collect()
    assert m.num_trades == 0
    assert m.total_pnl == Decimal(0)
    assert m.total_fees == Decimal(0)
    assert m.win_rate is None
    assert m.avg_win == Decimal(0)
    assert m.avg_loss == Decimal(0)
    assert m.lookback_hours == 24


@pytest.mark.asyncio
async def test_mixed_trades_compute_win_rate_and_averages():
    rows = [
        _Row(fee="0.5", realized_pnl="10"),
        _Row(fee="0.5", realized_pnl="20"),
        _Row(fee="0.5", realized_pnl="30"),
        _Row(fee="0.5", realized_pnl="-15"),
    ]
    collector = MetricsCollector(session_factory=_factory(rows), lookback_hours=24)
    m = await collector.collect()
    assert m.num_trades == 4
    assert m.win_count == 3
    assert m.loss_count == 1
    assert m.win_rate == pytest.approx(0.75)
    assert m.total_pnl == Decimal(45)
    assert m.total_fees == Decimal("2.0")
    assert m.avg_win == Decimal(20)
    assert m.avg_loss == Decimal(-15)


@pytest.mark.asyncio
async def test_trades_with_null_pnl_are_counted_but_not_decided():
    """SPOT trades têm realized_pnl=None — contam no total, não no win_rate."""
    rows = [
        _Row(fee="0.1", realized_pnl=None),
        _Row(fee="0.1", realized_pnl=None),
        _Row(fee="0.1", realized_pnl="5"),
    ]
    collector = MetricsCollector(session_factory=_factory(rows), lookback_hours=24)
    m = await collector.collect()
    assert m.num_trades == 3
    assert m.win_count == 1
    assert m.loss_count == 0
    assert m.win_rate == pytest.approx(1.0)
    assert m.total_pnl == Decimal(5)


@pytest.mark.asyncio
async def test_as_dict_returns_floats():
    rows = [_Row(fee="1", realized_pnl="10")]
    collector = MetricsCollector(session_factory=_factory(rows), lookback_hours=24)
    m = await collector.collect()
    d = m.as_dict()
    assert isinstance(d["num_trades"], float)
    assert isinstance(d["total_pnl"], float)
    assert isinstance(d["win_rate"], float)


@pytest.mark.asyncio
async def test_collect_uses_now_for_lookback():
    collector = MetricsCollector(session_factory=_factory([]), lookback_hours=6)
    now = datetime(2025, 1, 1, 12, tzinfo=UTC)
    m = await collector.collect(now=now)
    assert m.computed_at == now
    assert m.lookback_hours == 6
    assert (now - timedelta(hours=6)).tzinfo is not None