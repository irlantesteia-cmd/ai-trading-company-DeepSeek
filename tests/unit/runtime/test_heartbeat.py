from datetime import UTC, datetime, timedelta

import pytest

from app.runtime.heartbeat import HeartbeatMonitor


def test_beat_records_last_seen():
    hb = HeartbeatMonitor(max_age_seconds=60.0)
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    hb.beat("a", now=t0)
    assert hb.last_seen("a") == t0
    assert hb.beat_count("a") == 1


def test_beat_increments_count():
    hb = HeartbeatMonitor()
    hb.beat("a")
    hb.beat("a")
    hb.beat("a")
    assert hb.beat_count("a") == 3


def test_is_stale_unknown_agent_is_true():
    hb = HeartbeatMonitor()
    assert hb.is_stale("nope")


def test_is_stale_after_max_age():
    hb = HeartbeatMonitor(max_age_seconds=10.0)
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    hb.beat("a", now=t0)
    assert not hb.is_stale("a", now=t0 + timedelta(seconds=5))
    assert hb.is_stale("a", now=t0 + timedelta(seconds=11))


def test_stale_agents_lists_only_stale():
    hb = HeartbeatMonitor(max_age_seconds=10.0)
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    hb.beat("fresh", now=t0 + timedelta(seconds=9))
    hb.beat("stale", now=t0)
    stale = hb.stale_agents(now=t0 + timedelta(seconds=10))
    assert stale == ["stale"]


def test_reset_clears():
    hb = HeartbeatMonitor()
    hb.beat("a")
    hb.beat("b")
    hb.reset("a")
    assert hb.known() == ["b"]
    hb.reset()
    assert hb.known() == []


def test_invalid_max_age():
    with pytest.raises(ValueError):
        HeartbeatMonitor(max_age_seconds=0)