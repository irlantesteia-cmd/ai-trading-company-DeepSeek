from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.enums import (
    MarginType,
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from app.domain.models.asset import TradingPair
from app.domain.models.market import (
    Candle,
    OrderBookLevel,
    OrderBookSnapshot,
    Ticker,
)
from app.domain.models.order import Order, OrderFill
from app.domain.models.position import FuturesPosition, SpotBalance


def _dt_ms(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _dec(v: Any) -> Decimal:
    return Decimal(str(v))


# --------------------------------------------------------------------- pares
def map_trading_pair(raw: dict, market_type: MarketType) -> TradingPair:
    filters = {f["filterType"]: f for f in raw.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
    price_filter = filters.get("PRICE_FILTER", {})

    if market_type is MarketType.SPOT:
        price_precision = int(raw.get("quotePrecision", 8))
        quantity_precision = int(raw.get("baseAssetPrecision", 8))
    else:
        price_precision = int(raw.get("pricePrecision", 8))
        quantity_precision = int(raw.get("quantityPrecision", 8))

    base = raw.get("baseAsset", "")
    quote = raw.get("quoteAsset", "")

    _ = price_filter  # reservado p/ validação de tick size no futuro

    return TradingPair(
        symbol=raw["symbol"],
        base=base,
        quote=quote,
        market_type=market_type,
        price_precision=price_precision,
        quantity_precision=quantity_precision,
        min_notional=_dec(notional.get("minNotional", "0")),
        min_quantity=_dec(lot.get("minQty", "0")),
        step_size=_dec(lot.get("stepSize", "0")),
        status=raw.get("status", "TRADING"),
    )


# ---------------------------------------------------------------------- market
def map_ticker(raw: dict, market_type: MarketType) -> Ticker:
    return Ticker(
        symbol=raw["symbol"],
        market_type=market_type,
        bid=_dec(raw.get("bidPrice", raw.get("b", "0"))),
        ask=_dec(raw.get("askPrice", raw.get("a", "0"))),
        last=_dec(raw.get("lastPrice", raw.get("c", "0"))),
        volume_24h=_dec(raw.get("volume", raw.get("v", "0"))),
        timestamp=_dt_ms(raw.get("closeTime", raw.get("E", 0))),
    )


def map_kline(raw: list, market_type: MarketType, symbol: str, interval: str) -> Candle:
    # Spot/Futures kline REST: [openTime, o, h, l, c, v, closeTime, ...]
    return Candle(
        symbol=symbol,
        market_type=market_type,
        interval=interval,
        open_time=_dt_ms(raw[0]),
        close_time=_dt_ms(raw[6]),
        open=_dec(raw[1]),
        high=_dec(raw[2]),
        low=_dec(raw[3]),
        close=_dec(raw[4]),
        volume=_dec(raw[5]),
        trades=int(raw[8]) if len(raw) > 8 else 0,
        closed=True,
    )


def map_kline_event(
    raw: dict, symbol: str, market_type: MarketType, interval: str
) -> Candle:
    k = raw["k"]
    return Candle(
        symbol=symbol,
        market_type=market_type,
        interval=interval,
        open_time=_dt_ms(k["t"]),
        close_time=_dt_ms(k["T"]),
        open=_dec(k["o"]),
        high=_dec(k["h"]),
        low=_dec(k["l"]),
        close=_dec(k["c"]),
        volume=_dec(k["v"]),
        trades=int(k["n"]),
        closed=bool(k["x"]),
    )


def map_order_book(
    raw: dict,
    symbol: str,
    market_type: MarketType,
    *,
    timestamp: datetime | None = None,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        market_type=market_type,
        timestamp=timestamp or datetime.now(UTC),
        last_update_id=int(raw.get("lastUpdateId", 0)),
        bids=[OrderBookLevel(price=_dec(p), quantity=_dec(q)) for p, q in raw["bids"]],
        asks=[OrderBookLevel(price=_dec(p), quantity=_dec(q)) for p, q in raw["asks"]],
    )


def map_ticker_event(raw: dict, symbol: str, market_type: MarketType) -> Ticker:
    return Ticker(
        symbol=symbol,
        market_type=market_type,
        bid=_dec(raw["b"]),
        ask=_dec(raw["a"]),
        last=_dec(raw["c"]),
        volume_24h=_dec(raw["v"]),
        timestamp=_dt_ms(raw["E"]),
    )


# ---------------------------------------------------------------------- orders
def map_order(raw: dict, market_type: MarketType) -> Order:
    fills_raw = raw.get("fills") or []
    fills = [
        OrderFill(
            price=_dec(f["price"]),
            quantity=_dec(f["qty"]),
            commission=_dec(f.get("commission", "0")),
            commission_asset=f.get("commissionAsset", ""),
            timestamp=datetime.now(UTC),
        )
        for f in fills_raw
    ]

    ts_ms = raw.get("updateTime") or raw.get("time") or raw.get("transactTime") or 0
    created_ms = raw.get("time") or raw.get("transactTime") or ts_ms

    return Order(
        exchange_order_id=str(raw.get("orderId", "")),
        client_order_id=str(raw.get("clientOrderId", "")),
        symbol=raw["symbol"],
        market_type=market_type,
        side=OrderSide(raw["side"]),
        type=OrderType(raw["type"]),
        status=OrderStatus(raw["status"]),
        quantity=_dec(raw.get("origQty", "0")),
        executed_quantity=_dec(raw.get("executedQty", "0")),
        price=_dec(raw["price"]) if raw.get("price") not in (None, "0", 0) else None,
        average_price=(
            _dec(raw["avgPrice"])
            if raw.get("avgPrice") not in (None, "0", 0)
            else None
        ),
        stop_price=(
            _dec(raw["stopPrice"])
            if raw.get("stopPrice") not in (None, "0", 0)
            else None
        ),
        time_in_force=(
            TimeInForce(raw["timeInForce"]) if raw.get("timeInForce") else None
        ),
        reduce_only=bool(raw.get("reduceOnly", False)),
        position_side=(
            PositionSide(raw["positionSide"]) if raw.get("positionSide") else None
        ),
        created_at=_dt_ms(created_ms) if created_ms else datetime.now(UTC),
        updated_at=_dt_ms(ts_ms) if ts_ms else datetime.now(UTC),
        fills=fills,
    )


def order_request_to_params(
    *,
    symbol: str,
    side: OrderSide,
    type_: OrderType,
    quantity: Decimal,
    client_order_id: str,
    market_type: MarketType,
    price: Decimal | None = None,
    stop_price: Decimal | None = None,
    time_in_force: TimeInForce | None = None,
    reduce_only: bool = False,
    position_side: PositionSide | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "symbol": symbol,
        "side": side.value,
        "type": type_.value,
        "quantity": str(quantity),
        "newClientOrderId": client_order_id,
    }
    if price is not None:
        params["price"] = str(price)
    if stop_price is not None:
        params["stopPrice"] = str(stop_price)
    if time_in_force is not None:
        params["timeInForce"] = time_in_force.value

    if market_type is MarketType.FUTURES:
        if reduce_only:
            params["reduceOnly"] = "true"
        if position_side is not None:
            params["positionSide"] = position_side.value
    else:
        # Spot: pedir resposta com fills
        params["newOrderRespType"] = "FULL"

    return params


# --------------------------------------------------------------------- account
def map_spot_balance(raw: dict) -> SpotBalance:
    return SpotBalance(
        asset=raw["asset"],
        free=_dec(raw["free"]),
        locked=_dec(raw["locked"]),
    )


def map_futures_balance(raw: dict) -> SpotBalance:
    total = _dec(raw.get("balance", "0"))
    available = _dec(raw.get("availableBalance", "0"))
    locked = total - available
    if locked < 0:
        locked = Decimal(0)
    return SpotBalance(asset=raw["asset"], free=available, locked=locked)


# Binance devolve "cross" (4 letras) e "isolated"; o domínio usa CROSSED/ISOLATED.
_MARGIN_ALIASES: dict[str, MarginType] = {
    "CROSS": MarginType.CROSSED,
    "CROSSED": MarginType.CROSSED,
    "ISOLATED": MarginType.ISOLATED,
}


def _map_margin_type(raw: dict) -> MarginType:
    margin_raw = raw.get("marginType", raw.get("isolated", False))
    if isinstance(margin_raw, bool):
        return MarginType.ISOLATED if margin_raw else MarginType.CROSSED
    key = str(margin_raw).strip().upper()
    return _MARGIN_ALIASES.get(key, MarginType.CROSSED)


def map_futures_position(raw: dict) -> FuturesPosition:
    amt = _dec(raw.get("positionAmt", "0"))
    explicit = raw.get("positionSide")
    if explicit and explicit != "BOTH":
        side = PositionSide(explicit)
    else:
        side = PositionSide.LONG if amt >= 0 else PositionSide.SHORT

    return FuturesPosition(
        symbol=raw["symbol"],
        position_side=side,
        quantity=abs(amt),
        entry_price=_dec(raw.get("entryPrice", "0")),
        mark_price=_dec(raw.get("markPrice", "0")),
        unrealized_pnl=_dec(raw.get("unRealizedProfit", "0")),
        realized_pnl=Decimal(0),
        leverage=int(raw.get("leverage", 1)),
        margin_type=_map_margin_type(raw),
        isolated_margin=_dec(raw.get("isolatedMargin", "0")),
        liquidation_price=(
            _dec(raw["liquidationPrice"])
            if raw.get("liquidationPrice") not in (None, "0", 0)
            else None
        ),
        updated_at=(
            _dt_ms(raw["updateTime"])
            if raw.get("updateTime")
            else datetime.now(UTC)
        ),
    )