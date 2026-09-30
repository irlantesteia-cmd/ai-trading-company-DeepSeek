from datetime import UTC, datetime
from decimal import Decimal

from app.core.enums import MarginType, PositionSide
from app.domain.models.position import FuturesPosition
from app.portfolio.exposure import ExposureTracker
from app.portfolio.state import PortfolioState


def _pos(symbol: str, qty: str, price: str) -> FuturesPosition:
    return FuturesPosition(
        symbol=symbol,
        position_side=PositionSide.LONG,
        quantity=Decimal(qty),
        entry_price=Decimal(price),
        mark_price=Decimal(price),
        unrealized_pnl=Decimal(0),
        realized_pnl=Decimal(0),
        leverage=5,
        margin_type=MarginType.ISOLATED,
        isolated_margin=Decimal(500),
        updated_at=datetime.now(UTC),
    )


def test_total_notional():
    state = PortfolioState(
        equity=Decimal(10000),
        daily_pnl=Decimal(0),
        positions=[_pos("BTCUSDT", "0.1", "60000"), _pos("ETHUSDT", "2", "3000")],
    )
    assert ExposureTracker.total_notional(state) == Decimal(12000)


def test_by_symbol():
    state = PortfolioState(
        equity=Decimal(10000),
        daily_pnl=Decimal(0),
        positions=[_pos("BTCUSDT", "0.1", "60000")],
    )
    assert ExposureTracker.by_symbol(state) == {"BTCUSDT": Decimal(6000)}


def test_by_group():
    state = PortfolioState(
        equity=Decimal(10000),
        daily_pnl=Decimal(0),
        positions=[_pos("BTCUSDT", "0.1", "60000"), _pos("ETHUSDT", "2", "3000")],
    )
    groups = {"crypto_majors": ["BTCUSDT", "ETHUSDT"]}
    assert ExposureTracker.by_group(state, groups) == {
        "crypto_majors": Decimal(12000)
    }