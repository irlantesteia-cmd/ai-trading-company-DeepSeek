from datetime import UTC, datetime
from decimal import Decimal

from app.core.enums import (
    MarginType,
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from app.exchanges.binance.mappers import (
    map_futures_position,
    map_kline,
    map_kline_event,
    map_order,
    map_order_book,
    map_ticker,
    order_request_to_params,
)


def test_map_ticker_spot():
    raw = {
        "symbol": "BTCUSDT",
        "bidPrice": "60000.10",
        "askPrice": "60000.20",
        "lastPrice": "60000.15",
        "volume": "1234.5",
        "closeTime": 1_700_000_000_000,
    }
    t = map_ticker(raw, MarketType.SPOT)
    assert t.symbol == "BTCUSDT"
    assert t.bid == Decimal("60000.10")
    assert t.last == Decimal("60000.15")
    assert t.timestamp.tzinfo == UTC


def test_map_kline_spot():
    row = [
        1_700_000_000_000,
        "1.0",
        "2.0",
        "0.5",
        "1.5",
        "100.0",
        1_700_000_059_999,
        "150.0",
        42,
        "50.0",
        "75.0",
        "0",
    ]
    c = map_kline(row, MarketType.SPOT, "BTCUSDT", "1m")
    assert c.close == Decimal("1.5")
    assert c.trades == 42
    assert c.taker_buy_base_volume == Decimal("50.0")
    assert c.closed is True


def test_map_kline_event_taker_buy():
    raw = {
        "k": {
            "t": 1_700_000_000_000,
            "T": 1_700_000_299_999,
            "o": "1.0",
            "h": "2.0",
            "l": "0.5",
            "c": "1.5",
            "v": "100.0",
            "n": 42,
            "x": True,
            "V": "40.0",
        }
    }
    c = map_kline_event(raw, "BTCUSDT", MarketType.FUTURES, "5m")
    assert c.taker_buy_base_volume == Decimal("40.0")
    assert c.closed is True


def test_map_order_book():
    raw = {
        "lastUpdateId": 999,
        "bids": [["100.0", "1.0"], ["99.5", "2.0"]],
        "asks": [["100.5", "0.5"]],
    }
    ob = map_order_book(raw, "BTCUSDT", MarketType.SPOT)
    assert ob.last_update_id == 999
    assert ob.bids[0].price == Decimal("100.0")
    assert ob.asks[0].quantity == Decimal("0.5")


def test_map_order_spot_with_fills():
    raw = {
        "orderId": 123,
        "clientOrderId": "c-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "status": "FILLED",
        "origQty": "1.0",
        "executedQty": "1.0",
        "price": "60000",
        "avgPrice": "60000",
        "timeInForce": "GTC",
        "time": 1_700_000_000_000,
        "updateTime": 1_700_000_001_000,
        "fills": [
            {
                "price": "60000",
                "qty": "1.0",
                "commission": "0.001",
                "commissionAsset": "BTC",
            }
        ],
    }
    o = map_order(raw, MarketType.SPOT)
    assert o.status is OrderStatus.FILLED
    assert o.side is OrderSide.BUY
    assert o.type is OrderType.LIMIT
    assert o.time_in_force is TimeInForce.GTC
    assert o.remaining_quantity == Decimal(0)
    assert o.fills[0].commission == Decimal("0.001")


def test_order_request_to_params_spot():
    params = order_request_to_params(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type_=OrderType.LIMIT,
        quantity=Decimal("0.5"),
        client_order_id="c-1",
        market_type=MarketType.SPOT,
        price=Decimal(60000),
        time_in_force=TimeInForce.GTC,
    )
    assert params["newOrderRespType"] == "FULL"
    assert params["price"] == "60000"
    assert params["timeInForce"] == "GTC"
    assert "reduceOnly" not in params


def test_order_request_futures_one_way_reduce_only():
    """One-way mode: reduceOnly=true sem positionSide."""
    params = order_request_to_params(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        type_=OrderType.MARKET,
        quantity=Decimal("0.5"),
        client_order_id="c-2",
        market_type=MarketType.FUTURES,
        reduce_only=True,
    )
    assert params["reduceOnly"] == "true"
    assert "positionSide" not in params
    assert "newOrderRespType" not in params


def test_order_request_futures_hedge_mode_no_reduce_only():
    """Hedge mode: positionSide=LONG/SHORT e SEM reduceOnly (-1106)."""
    params = order_request_to_params(
        symbol="XRPUSDT",
        side=OrderSide.BUY,
        type_=OrderType.MARKET,
        quantity=Decimal("6670.6"),
        client_order_id="c-3",
        market_type=MarketType.FUTURES,
        position_side=PositionSide.SHORT,
        reduce_only=True,  # deve ser ignorado em hedge mode
    )
    assert params["positionSide"] == "SHORT"
    assert "reduceOnly" not in params


def test_order_request_market_omits_stop_price():
    """MARKET não aceita stopPrice (-1106). Ignorado silenciosamente."""
    params = order_request_to_params(
        symbol="XRPUSDT",
        side=OrderSide.SELL,
        type_=OrderType.MARKET,
        quantity=Decimal("6717.7"),
        client_order_id="c-4",
        market_type=MarketType.FUTURES,
        stop_price=Decimal("1.4935"),
    )
    assert "stopPrice" not in params


def test_order_request_stop_market_keeps_stop_price():
    params = order_request_to_params(
        symbol="XRPUSDT",
        side=OrderSide.SELL,
        type_=OrderType.STOP_MARKET,
        quantity=Decimal("6717.7"),
        client_order_id="c-5",
        market_type=MarketType.FUTURES,
        stop_price=Decimal("1.4935"),
    )
    assert params["stopPrice"] == "1.4935"


def test_order_request_take_profit_market_keeps_stop_price():
    params = order_request_to_params(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        type_=OrderType.TAKE_PROFIT_MARKET,
        quantity=Decimal("0.1"),
        client_order_id="c-6",
        market_type=MarketType.FUTURES,
        stop_price=Decimal(65000),
    )
    assert params["stopPrice"] == "65000"


def test_map_futures_position_long():
    raw = {
        "symbol": "BTCUSDT",
        "positionAmt": "0.5",
        "entryPrice": "60000",
        "markPrice": "61000",
        "unRealizedProfit": "500",
        "leverage": "10",
        "marginType": "isolated",
        "isolatedMargin": "3000",
        "liquidationPrice": "50000",
        "positionSide": "BOTH",
        "updateTime": 1_700_000_000_000,
    }
    pos = map_futures_position(raw)
    assert pos.position_side is PositionSide.LONG
    assert pos.margin_type is MarginType.ISOLATED
    assert pos.quantity == Decimal("0.5")
    assert pos.notional == Decimal("0.5") * Decimal(61000)
    assert pos.is_open is True


def test_map_futures_position_short_from_negative_amt():
    raw = {
        "symbol": "ETHUSDT",
        "positionAmt": "-2",
        "entryPrice": "3000",
        "markPrice": "2950",
        "unRealizedProfit": "100",
        "leverage": "5",
        "marginType": "cross",
        "isolatedMargin": "0",
        "positionSide": "BOTH",
        "updateTime": 1_700_000_000_000,
    }
    pos = map_futures_position(raw)
    assert pos.position_side is PositionSide.SHORT
    assert pos.margin_type is MarginType.CROSSED
    assert pos.quantity == Decimal(2)


def test_map_futures_position_crossed_margin_aliases():
    for alias in ("cross", "CROSS", "crossed", "CROSSED"):
        raw = {
            "symbol": "SOLUSDT",
            "positionAmt": "1",
            "entryPrice": "100",
            "markPrice": "101",
            "unRealizedProfit": "1",
            "leverage": "3",
            "marginType": alias,
            "isolatedMargin": "0",
            "positionSide": "BOTH",
            "updateTime": 1_700_000_000_000,
        }
        assert map_futures_position(raw).margin_type is MarginType.CROSSED


def test_map_futures_position_isolated_bool_fallback():
    raw_isolated = {
        "symbol": "XRPUSDT",
        "positionAmt": "10",
        "entryPrice": "0.5",
        "markPrice": "0.51",
        "unRealizedProfit": "0.1",
        "leverage": "2",
        "isolated": True,
        "isolatedMargin": "2.5",
        "positionSide": "BOTH",
        "updateTime": 1_700_000_000_000,
    }
    assert map_futures_position(raw_isolated).margin_type is MarginType.ISOLATED

    raw_cross = dict(raw_isolated, isolated=False)
    assert map_futures_position(raw_cross).margin_type is MarginType.CROSSED

def _kline_row(open_ms: int, close_ms: int) -> list:
    return [open_ms, "1.0", "2.0", "0.5", "1.5", "100.0", close_ms, "150.0", 42, "50.0"]


def test_map_kline_in_progress_bar_is_not_closed():
    open_ms = 1_700_000_000_000
    close_ms = open_ms + 299_999
    before_close = datetime.fromtimestamp((close_ms - 1) / 1000, tz=UTC)
    after_close = datetime.fromtimestamp((close_ms + 1) / 1000, tz=UTC)
    row = _kline_row(open_ms, close_ms)

    assert map_kline(row, MarketType.FUTURES, "BTCUSDT", "5m", now=before_close).closed is False
    assert map_kline(row, MarketType.FUTURES, "BTCUSDT", "5m", now=after_close).closed is True


def test_map_kline_defaults_to_wall_clock():
    far_future_ms = 4_102_444_800_000  # 2100-01-01
    row = _kline_row(far_future_ms, far_future_ms + 299_999)
    assert map_kline(row, MarketType.FUTURES, "BTCUSDT", "5m").closed is False
