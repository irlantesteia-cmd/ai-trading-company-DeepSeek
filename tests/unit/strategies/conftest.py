from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.enums import MarketType
from app.domain.models.market import Candle


def make_candles(
    closes: list[float],
    *,
    interval: str = "5m",
    market_type: MarketType = MarketType.FUTURES,
    symbol: str = "BTCUSDT",
    start: datetime | None = None,
) -> list[Candle]:
    """Fábrica determinística de candles: `open = close anterior`, high/low ±0.1%."""
    out: list[Candle] = []
    t0 = start or datetime(2024, 1, 1, tzinfo=UTC)
    for i, c in enumerate(closes):
        d = Decimal(str(c))
        prev = Decimal(str(closes[i - 1])) if i > 0 else d
        high = max(d, prev) * Decimal("1.001")
        low = min(d, prev) * Decimal("0.999")
        out.append(
            Candle(
                symbol=symbol,
                market_type=market_type,
                interval=interval,
                open_time=t0 + timedelta(minutes=5 * i),
                close_time=t0 + timedelta(minutes=5 * i + 5),
                open=prev,
                high=high,
                low=low,
                close=d,
                volume=Decimal(1),
                trades=1,
                taker_buy_base_volume=Decimal("0.5"),
                closed=True,
            )
        )
    return out


@pytest.fixture
def candles_factory():
    return make_candles