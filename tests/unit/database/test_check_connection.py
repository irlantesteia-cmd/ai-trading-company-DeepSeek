import asyncio

import pytest

from app.database import session as session_mod


class _FakeConn:
    def __init__(self, *, raise_on_execute: Exception | None = None) -> None:
        self._raise = raise_on_execute
        self.executed = False

    async def execute(self, _stmt) -> None:
        self.executed = True
        if self._raise is not None:
            raise self._raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeEngine:
    def __init__(self, *, raise_on_connect: Exception | None = None,
                 raise_on_execute: Exception | None = None) -> None:
        self._raise_connect = raise_on_connect
        self._raise_execute = raise_on_execute
        self.conn = _FakeConn(raise_on_execute=raise_on_execute)

    def connect(self):
        if self._raise_connect is not None:
            raise self._raise_connect
        return self.conn


@pytest.mark.asyncio
async def test_check_connection_ok(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr(session_mod, "engine", fake)
    await session_mod.check_connection()
    assert fake.conn.executed is True


@pytest.mark.asyncio
async def test_check_connection_raises_on_connect_error(monkeypatch):
    fake = _FakeEngine(raise_on_connect=ConnectionRefusedError("boom"))
    monkeypatch.setattr(session_mod, "engine", fake)
    with pytest.raises(ConnectionRefusedError):
        await session_mod.check_connection()


@pytest.mark.asyncio
async def test_check_connection_raises_on_execute_error(monkeypatch):
    fake = _FakeEngine(raise_on_execute=RuntimeError("sql failed"))
    monkeypatch.setattr(session_mod, "engine", fake)
    with pytest.raises(RuntimeError, match="sql failed"):
        await session_mod.check_connection()


@pytest.mark.asyncio
async def test_check_connection_times_out(monkeypatch):
    class _SlowConn(_FakeConn):
        async def execute(self, _stmt) -> None:
            await asyncio.sleep(10.0)

    class _SlowEngine:
        def connect(self):
            return _SlowConn()

    monkeypatch.setattr(session_mod, "engine", _SlowEngine())
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await session_mod.check_connection(timeout=0.05)