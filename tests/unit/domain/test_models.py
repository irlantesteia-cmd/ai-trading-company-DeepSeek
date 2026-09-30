from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.enums import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SignalDirection,
)
from app.domain.models.order import Order
from app.domain.models.position import FuturesPosition, SpotBalance
from app.domain.models.signal import Signal


def test_spot_balance_total():
    b = SpotBalance(asset="USDT", free=Decimal("100.5"), locked=Decimal(20))
    assert b.total == Decimal("120.5")


def test_futures_position_notional_and_is_open():
    pos = FuturesPosition(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal(60000),
        mark_price=Decimal(61000),
        unrealized_pnl=Decimal(500),
        realized_pnl=Decimal(0),
        leverage=10,
        margin_type="ISOLATED",
        isolated_margin=Decimal(3000),
        updated_at=datetime.now(UTC),
    )
    assert pos.notional == Decimal(30500)
    assert pos.is_open is True


def test_order_is_open_and_remaining():
    o = Order(
        exchange_order_id="1",
        client_order_id="c1",
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        status=OrderStatus.PARTIALLY_FILLED,
        quantity=Decimal(1),
        executed_quantity=Decimal("0.4"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert o.is_open is True
    assert o.remaining_quantity == Decimal("0.6")


def test_domain_model_is_frozen():
    b = SpotBalance(asset="BTC", free=Decimal(1), locked=Decimal(0))
    with pytest.raises(ValidationError):
        b.asset = "ETH"  # type: ignore[misc]


def test_signal_requires_confidence():
    s = Signal(
        signal_id="s1",
        symbol="ETHUSDT",
        market_type=MarketType.FUTURES,
        direction=SignalDirection.LONG,
        confidence=0.7,
        horizon="5m",
        strategy="momentum",
        agent="ETH-agent",
        generated_at=datetime.now(UTC),
    )
    assert 0.0 <= s.confidence <= 1.0