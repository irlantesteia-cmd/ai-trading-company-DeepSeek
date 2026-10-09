from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.market.backfill import HistoryBackfillService


def _candles(symbol: str = "BTCUSDT", n: int = 5) -> list[Candle]:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol=symbol,
            market_type=MarketType.FUTURES,
            interval="5m",
            open_time=t0 + timedelta(minutes=5 * i),
            close_time=t0 + timedelta(minutes=5 * i + 5),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
            trades=10,
            closed=True,
        )
        for i in range(n)
    ]


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.executed_statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        # Simula rowcount = 5 inseridos
        result = MagicMock()
        result.rowcount = 5
        return result

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        pass


def _service(candles_by_symbol: dict[str, list[Candle]]) -> tuple[HistoryBackfillService, list]:
    sessions: list = []

    def _factory():
        s = _FakeSession()
        sessions.append(s)
        return s

    exchange = MagicMock()

    async def _get_candles(symbol, interval, market_type, limit=500):
        return candles_by_symbol.get(symbol, [])

    exchange.market_data.get_candles = AsyncMock(side_effect=_get_candles)

    service = HistoryBackfillService(
        exchange=exchange, session_factory=_factory
    )
    return service, sessions


@pytest.mark.asyncio
async def test_backfill_persists_all_symbols():
    candles_map = {
        "BTCUSDT": _candles("BTCUSDT", 5),
        "ETHUSDT": _candles("ETHUSDT", 5),
    }
    service, sessions = _service(candles_map)

    report = await service.backfill(
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="5m",
        market_type=MarketType.FUTURES,
        limit=500,
    )
    assert report.symbols_processed == 2
    assert report.candles_fetched == 10
    assert report.candles_persisted == 10  # _FakeSession devolve rowcount 5 por chamada
    assert len(sessions) == 2
    assert all(s.committed for s in sessions)


@pytest.mark.asyncio
async def test_backfill_skips_symbols_with_no_candles():
    service, sessions = _service({"BTCUSDT": []})
    report = await service.backfill(
        symbols=["BTCUSDT"],
        interval="5m",
        market_type=MarketType.FUTURES,
    )
    assert report.symbols_processed == 1
    assert report.candles_fetched == 0
    # Não abre sessão quando não há candles para inserir
    assert len(sessions) == 0


@pytest.mark.asyncio
async def test_backfill_continues_on_symbol_failure():
    """Falha num símbolo não aborta o backfill dos demais."""
    calls = {"n": 0}

    def _factory():
        return _FakeSession()

    exchange = MagicMock()

    async def _get_candles(symbol, interval, market_type, limit=500):
        calls["n"] += 1
        if symbol == "BADUSDT":
            raise RuntimeError("network")
        return _candles(symbol, 3)

    exchange.market_data.get_candles = AsyncMock(side_effect=_get_candles)

    service = HistoryBackfillService(
        exchange=exchange, session_factory=_factory
    )
    report = await service.backfill(
        symbols=["BADUSDT", "BTCUSDT"],
        interval="5m",
        market_type=MarketType.FUTURES,
    )
    # BADUSDT falhou (não conta em processed), BTCUSDT processou
    assert report.symbols_processed == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_backfill_report_duplicates_skipped():
    from app.market.backfill import BackfillReport

    report = BackfillReport(
        symbols_processed=1, candles_fetched=10, candles_persisted=7
    )
    assert report.duplicates_skipped == 3


@pytest.mark.asyncio
async def test_backfill_skips_in_progress_candle(monkeypatch):
    candles = _candles("BTCUSDT", 4)
    candles[-1] = candles[-1].model_copy(update={"closed": False})
    service, _ = _service({"BTCUSDT": candles})

    captured: list[list[dict]] = []

    async def _fake_upsert(self, rows):
        captured.append(rows)
        return len(rows)

    monkeypatch.setattr(
        "app.market.backfill.CandleRepository.bulk_upsert", _fake_upsert
    )

    report = await service.backfill(
        symbols=["BTCUSDT"],
        interval="5m",
        market_type=MarketType.FUTURES,
    )

    assert report.candles_fetched == 3
    assert [r["open_time"] for r in captured[0]] == [c.open_time for c in candles[:3]]
