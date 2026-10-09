from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents.signal_recorder import (
    _MAX_PENDING,
    APPROVED,
    IGNORED,
    REJECTED,
    SignalRecorderAgent,
)
from app.core.enums import MarketType, SignalDirection
from app.domain.models.order_intent import OrderIntent
from app.domain.models.signal import Signal
from app.events.event import (
    OrderIntentCreated,
    SignalApproved,
    SignalGenerated,
    SignalRejected,
)


def _signal(signal_id: str = "sig-1") -> Signal:
    return Signal(
        signal_id=signal_id,
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        confidence=0.7,
        horizon="5m",
        strategy="ml",
        agent="asset::SOLUSDT",
        generated_at=datetime.now(UTC),
    )


def _generated(signal_id: str = "sig-1") -> SignalGenerated:
    return SignalGenerated(
        signal_id=signal_id,
        symbol="SOLUSDT",
        agent="asset::SOLUSDT",
        direction="LONG",
        confidence=0.7,
        signal=_signal(signal_id),
    )


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self) -> None:
        pass


class _FakeRepo:
    """Simula `signals`: guarda inserts e só atualiza sinais já gravados."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.fail = False

    def bind(self, monkeypatch) -> None:
        repo = self

        async def insert(_self, signal, **fields):
            if repo.fail:
                raise RuntimeError("db down")
            repo.rows.setdefault(signal.signal_id, {"signal": signal, **fields})

        async def update_fields(_self, signal_id, **fields):
            if repo.fail:
                raise RuntimeError("db down")
            if signal_id not in repo.rows:
                return False
            repo.rows[signal_id].update(fields)
            return True

        monkeypatch.setattr("app.agents.signal_recorder.SignalRepository.insert", insert)
        monkeypatch.setattr(
            "app.agents.signal_recorder.SignalRepository.update_fields", update_fields
        )


@pytest.fixture
def recorder(context, monkeypatch):
    context.session_factory = _FakeSession
    monkeypatch.setattr(context.settings, "signal_auto_execution_enabled", True)
    repo = _FakeRepo()
    repo.bind(monkeypatch)
    return SignalRecorderAgent(context), repo


async def test_generated_then_rejected(recorder):
    agent, repo = recorder
    await agent._on_signal_generated(_generated())
    await agent._on_signal_rejected(SignalRejected(signal_id="sig-1", reason="max_open"))

    assert repo.rows["sig-1"]["decision"] == REJECTED
    assert repo.rows["sig-1"]["decision_reason"] == "max_open"


async def test_decision_before_insert_is_applied_on_insert(recorder):
    """TradingManager decide dentro do próprio handler de SignalGenerated."""
    agent, repo = recorder
    intent = OrderIntent(
        signal_id="sig-1",
        symbol="SOLUSDT",
        market_type=MarketType.FUTURES,
        side="BUY",
        quantity=Decimal(2),
    )
    await agent._on_signal_approved(SignalApproved(signal_id="sig-1", approved_quantity=2.0))
    await agent._on_order_intent(
        OrderIntentCreated(
            intent_id=intent.intent_id, signal_id="sig-1", symbol="SOLUSDT",
            side="BUY", quantity=2.0,
        )
    )
    assert repo.rows == {}

    await agent._on_signal_generated(_generated())

    row = repo.rows["sig-1"]
    assert row["decision"] == APPROVED
    assert row["approved_quantity"] == Decimal("2.0")
    # Mesmo client_order_id que o ExecutionAgent envia para a exchange.
    assert row["client_order_id"] == intent.client_order_id
    assert agent._pending == {}


async def test_ignored_when_auto_execution_disabled(recorder, monkeypatch):
    agent, repo = recorder
    monkeypatch.setattr(agent.context.settings, "signal_auto_execution_enabled", False)

    await agent._on_signal_generated(_generated())

    assert repo.rows["sig-1"]["decision"] == IGNORED


async def test_db_failure_is_isolated(recorder, caplog):
    agent, repo = recorder
    repo.fail = True

    await agent._on_signal_generated(_generated())
    await agent._on_signal_rejected(SignalRejected(signal_id="sig-1", reason="x"))

    assert "signal_recorder.persist_failed" in caplog.text
    assert "signal_recorder.update_failed" in caplog.text


async def test_pending_updates_are_bounded(recorder):
    agent, _ = recorder
    for i in range(_MAX_PENDING + 5):
        await agent._on_signal_rejected(SignalRejected(signal_id=f"s{i}", reason="x"))

    assert len(agent._pending) == _MAX_PENDING
    assert "s0" not in agent._pending  # os mais antigos saem primeiro


async def test_missing_payload_is_skipped(recorder):
    agent, repo = recorder
    event = _generated().model_copy(update={"signal": None})
    await agent._on_signal_generated(event)
    assert repo.rows == {}
