from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.database.repositories.candle import CandleRepository


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        result = MagicMock()
        result.rowcount = 1
        return result

    async def flush(self) -> None:
        pass


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_bulk_upsert_refreshes_updated_at():
    session = _CapturingSession()
    t = datetime(2024, 1, 1, tzinfo=UTC)
    await CandleRepository(session).bulk_upsert(
        [
            {
                "symbol": "BTCUSDT",
                "market_type": "FUTURES",
                "interval": "5m",
                "open_time": t,
                "close_time": t,
                "open": Decimal(1),
                "high": Decimal(1),
                "low": Decimal(1),
                "close": Decimal(1),
                "volume": Decimal(1),
                "trades": 1,
                "taker_buy_base_volume": None,
            }
        ]
    )
    sql = _sql(session.statements[0])
    assert "ON CONFLICT ON CONSTRAINT uq_candle_symbol_market_interval_time DO UPDATE" in sql
    assert "updated_at = now()" in sql


async def test_list_persisted_while_open_filters_on_timestamps():
    session = _CapturingSession()
    session.execute = _scalars_returning([])(session)
    await CandleRepository(session).list_persisted_while_open()
    sql = _sql(session.statements[0])
    assert "candles.created_at < candles.close_time" in sql
    assert "candles.updated_at < candles.close_time" in sql


def _scalars_returning(items):
    def _bind(session):
        async def _execute(stmt):
            session.statements.append(stmt)
            result = MagicMock()
            result.scalars.return_value.all.return_value = items
            return result

        return _execute

    return _bind
