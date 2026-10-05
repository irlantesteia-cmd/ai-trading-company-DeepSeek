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
from app.domain.models.market import Candle, OrderBookLevel, OrderBookSnapshot, Ticker
from app.domain.models.order import Order, OrderFill
from app.domain.models.position import FuturesPosition, SpotBalance

_STOP_ORDER_TYPES: frozenset[OrderType] = frozenset({
    OrderType.STOP_MARKET, OrderType.STOP_LIMIT,
    OrderType.TAKE_PROFIT_MARKET, OrderType.TAKE_PROFIT_LIMIT,
    OrderType.TRAILING_STOP_MARKET,
})

_CLOSE_POSITION_ORDER_TYPES: frozenset[OrderType] = frozenset({
    OrderType.STOP_MARKET,
    OrderType.TAKE_PROFIT_MARKET,
    OrderType.TRAILING_STOP_MARKET,
})

_ALGO_TYPES: frozenset[OrderType] = frozenset({
    OrderType.STOP_MARKET, OrderType.STOP_LIMIT,
    OrderType.TAKE_PROFIT_MARKET, OrderType.TAKE_PROFIT_LIMIT,
    OrderType.TRAILING_STOP_MARKET,
})


def _dt_ms(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _dec(v: Any) -> Decimal:
    return Decimal(str(v))


def _safe_time_in_force(raw_val: str | None) -> TimeInForce | None:
    """Converte `timeInForce` para enum, retornando None se desconhecido.

    A Algo Order API usa valores como `GTE_GTC` que não estão no enum
    padrão. Não queremos quebrar o parse por causa disso — `None` é
    aceitável no domínio `Order`.
    """
    if not raw_val:
        return None
    try:
        return TimeInForce(raw_val)
    except ValueError:
        return None


def map_trading_pair(raw: dict, market_type: MarketType) -> TradingPair:
    filters = {f["filterType"]: f for f in raw.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
    if market_type is MarketType.SPOT:
        price_precision = int(raw.get("quotePrecision", 8))
        quantity_precision = int(raw.get("baseAssetPrecision", 8))
    else:
        price_precision = int(raw.get("pricePrecision", 8))
        quantity_precision = int(raw.get("quantityPrecision", 8))

    return TradingPair(
        symbol=raw["symbol"], base=raw.get("baseAsset", ""), quote=raw.get("quoteAsset", ""),
        market_type=market_type, price_precision=price_precision,
        quantity_precision=quantity_precision,
        min_notional=_dec(notional.get("minNotional", "0")),
        min_quantity=_dec(lot.get("minQty", "0")),
        step_size=_dec(lot.get("stepSize", "0")),
        status=raw.get("status", "TRADING"),
    )


def map_ticker(raw: dict, market_type: MarketType) -> Ticker:
    return Ticker(
        symbol=raw["symbol"], market_type=market_type,
        bid=_dec(raw.get("bidPrice", raw.get("b", "0"))),
        ask=_dec(raw.get("askPrice", raw.get("a", "0"))),
        last=_dec(raw.get("lastPrice", raw.get("c", "0"))),
        volume_24h=_dec(raw.get("volume", raw.get("v", "0"))),
        timestamp=_dt_ms(raw.get("closeTime", raw.get("E", 0))),
    )


def map_kline(raw: list, market_type: MarketType, symbol: str, interval: str) -> Candle:
    return Candle(
        symbol=symbol, market_type=market_type, interval=interval,
        open_time=_dt_ms(raw[0]), close_time=_dt_ms(raw[6]),
        open=_dec(raw[1]), high=_dec(raw[2]), low=_dec(raw[3]), close=_dec(raw[4]),
        volume=_dec(raw[5]), trades=int(raw[8]) if len(raw) > 8 else 0, closed=True,
    )


def map_kline_event(raw: dict, symbol: str, market_type: MarketType, interval: str) -> Candle:
    k = raw["k"]
    return Candle(
        symbol=symbol, market_type=market_type, interval=interval,
        open_time=_dt_ms(k["t"]), close_time=_dt_ms(k["T"]),
        open=_dec(k["o"]), high=_dec(k["h"]), low=_dec(k["l"]), close=_dec(k["c"]),
        volume=_dec(k["v"]), trades=int(k["n"]), closed=bool(k["x"]),
    )


def map_order_book(raw, symbol, market_type, *, timestamp=None) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol, market_type=market_type,
        timestamp=timestamp or datetime.now(UTC),
        last_update_id=int(raw.get("lastUpdateId", 0)),
        bids=[OrderBookLevel(price=_dec(p), quantity=_dec(q)) for p, q in raw["bids"]],
        asks=[OrderBookLevel(price=_dec(p), quantity=_dec(q)) for p, q in raw["asks"]],
    )


def map_ticker_event(raw: dict, symbol: str, market_type: MarketType) -> Ticker:
    return Ticker(
        symbol=symbol, market_type=market_type,
        bid=_dec(raw["b"]), ask=_dec(raw["a"]), last=_dec(raw["c"]),
        volume_24h=_dec(raw["v"]), timestamp=_dt_ms(raw["E"]),
    )


def map_order(raw: dict, market_type: MarketType) -> Order:
    """Mapeia resposta de `/fapi/v1/order`, `/fapi/v1/algoOrder` ou listings.

    Aceita aliases da Algo Order API:
      - `orderId` / `algoId`
      - `clientOrderId` / `clientAlgoId`
      - `type` / `orderType`
      - `status` / `algoStatus`
      - `stopPrice` / `triggerPrice`
      - `timeInForce` tolerante (GTE_GTC vira `None`)
    """
    fills_raw = raw.get("fills") or []
    fills = [
        OrderFill(
            price=_dec(f["price"]), quantity=_dec(f["qty"]),
            commission=_dec(f.get("commission", "0")),
            commission_asset=f.get("commissionAsset", ""),
            timestamp=datetime.now(UTC),
            trade_id=str(f["id"]) if f.get("id") is not None else None,
        ) for f in fills_raw
    ]

    exchange_order_id = str(raw.get("orderId") or raw.get("algoId") or "")
    client_order_id = str(raw.get("clientOrderId") or raw.get("clientAlgoId") or "")

    raw_type = raw.get("type") or raw.get("orderType")
    raw_status = raw.get("status") or raw.get("algoStatus")

    ts_ms = (
        raw.get("updateTime") or raw.get("time") or raw.get("transactTime")
        or raw.get("bookTime") or 0
    )
    created_ms = raw.get("time") or raw.get("transactTime") or raw.get("bookTime") or ts_ms

    trigger = raw.get("stopPrice") or raw.get("triggerPrice")

    return Order(
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        symbol=raw["symbol"], market_type=market_type,
        side=OrderSide(raw["side"]), type=OrderType(raw_type),
        status=OrderStatus(raw_status),
        quantity=_dec(raw.get("origQty", raw.get("quantity", "0"))),
        executed_quantity=_dec(raw.get("executedQty", "0")),
        price=_dec(raw["price"]) if raw.get("price") not in (None, "0", 0) else None,
        average_price=_dec(raw["avgPrice"]) if raw.get("avgPrice") not in (None, "0", 0) else None,
        stop_price=_dec(trigger) if trigger not in (None, "0", 0) else None,
        time_in_force=_safe_time_in_force(raw.get("timeInForce")),
        reduce_only=bool(raw.get("reduceOnly", False)),
        position_side=PositionSide(raw["positionSide"]) if raw.get("positionSide") else None,
        created_at=_dt_ms(created_ms) if created_ms else datetime.now(UTC),
        updated_at=_dt_ms(ts_ms) if ts_ms else datetime.now(UTC),
        fills=fills,
    )


def map_user_trade(raw: dict) -> OrderFill:
    """Mapeia um item de `GET /fapi/v1/userTrades` para `OrderFill`."""
    return OrderFill(
        price=_dec(raw["price"]),
        quantity=_dec(raw["qty"]),
        commission=_dec(raw.get("commission", "0")),
        commission_asset=raw.get("commissionAsset", ""),
        timestamp=_dt_ms(raw["time"]),
        trade_id=str(raw["id"]) if raw.get("id") is not None else None,
    )


def map_order_trade_update(raw: dict, market_type: MarketType) -> Order | None:
    """Converte um evento `ORDER_TRADE_UPDATE` do UDS em `Order` com 1 fill."""
    o = raw.get("o") or {}
    if o.get("x") != "TRADE":
        return None

    last_qty = _dec(o.get("l", "0"))
    if last_qty <= 0:
        return None

    fill = OrderFill(
        price=_dec(o.get("L", "0")),
        quantity=last_qty,
        commission=_dec(o.get("n", "0")),
        commission_asset=o.get("N") or "",
        timestamp=_dt_ms(o.get("T") or raw.get("E") or 0),
        trade_id=str(o["t"]) if o.get("t") is not None else None,
    )

    event_ms = raw.get("E") or 0
    order_ms = o.get("T") or event_ms

    return Order(
        exchange_order_id=str(o.get("i", "")),
        client_order_id=str(o.get("c", "")),
        symbol=o["s"],
        market_type=market_type,
        side=OrderSide(o["S"]),
        type=OrderType(o["o"]),
        status=OrderStatus(o["X"]),
        quantity=_dec(o.get("q", "0")),
        executed_quantity=_dec(o.get("z", "0")),
        price=_dec(o["p"]) if o.get("p") not in (None, "0", 0) else None,
        average_price=_dec(o["ap"]) if o.get("ap") not in (None, "0", 0) else None,
        stop_price=_dec(o["sp"]) if o.get("sp") not in (None, "0", 0) else None,
        time_in_force=_safe_time_in_force(o.get("f")),
        reduce_only=bool(o.get("R", False)),
        position_side=PositionSide(o["ps"]) if o.get("ps") else None,
        created_at=_dt_ms(order_ms) if order_ms else datetime.now(UTC),
        updated_at=_dt_ms(event_ms) if event_ms else datetime.now(UTC),
        fills=[fill],
    )


def order_request_to_params(
    *, symbol: str, side: OrderSide, type_: OrderType, quantity: Decimal,
    client_order_id: str, market_type: MarketType,
    price: Decimal | None = None, stop_price: Decimal | None = None,
    time_in_force: TimeInForce | None = None,
    reduce_only: bool = False, position_side: PositionSide | None = None,
    close_position: bool = False,
) -> dict[str, Any]:
    """Regras de encoding para `/fapi/v1/order` (MARKET/LIMIT)."""
    params: dict[str, Any] = {
        "symbol": symbol, "side": side.value, "type": type_.value,
        "newClientOrderId": client_order_id,
    }

    use_close_position = (
        close_position
        and market_type is MarketType.FUTURES
        and type_ in _CLOSE_POSITION_ORDER_TYPES
    )

    if use_close_position:
        params["closePosition"] = "true"
    else:
        params["quantity"] = str(quantity)

    if price is not None:
        params["price"] = str(price)
    if stop_price is not None and type_ in _STOP_ORDER_TYPES:
        params["stopPrice"] = str(stop_price)
    if time_in_force is not None:
        params["timeInForce"] = time_in_force.value

    if market_type is MarketType.FUTURES:
        if use_close_position:
            pass
        elif position_side in (PositionSide.LONG, PositionSide.SHORT):
            params["positionSide"] = position_side.value
        elif reduce_only:
            params["reduceOnly"] = "true"
    else:
        params["newOrderRespType"] = "FULL"

    return params


def order_request_to_algo_params(
    *,
    symbol: str,
    side: OrderSide,
    type_: OrderType,
    quantity: Decimal,
    client_order_id: str,
    trigger_price: Decimal,
    reduce_only: bool = False,
    position_side: PositionSide | None = None,
    close_position: bool = False,
    working_type: str = "MARK_PRICE",
) -> dict[str, Any]:
    """Encoding para `POST /fapi/v1/algoOrder` (STOP_MARKET/TP_MARKET/trailing)."""
    if type_ not in _ALGO_TYPES:
        raise ValueError(
            f"tipo {type_.value} não é suportado em /fapi/v1/algoOrder"
        )

    params: dict[str, Any] = {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side.value,
        "type": type_.value,
        "triggerPrice": str(trigger_price),
        "clientAlgoId": client_order_id,
        "workingType": working_type,
    }

    if close_position:
        params["closePosition"] = "true"
    else:
        params["quantity"] = str(quantity)

    if close_position:
        pass
    elif position_side in (PositionSide.LONG, PositionSide.SHORT):
        params["positionSide"] = position_side.value
    elif reduce_only:
        params["reduceOnly"] = "true"

    return params


# --------------------------------------------------------------------- account
def map_spot_balance(raw: dict) -> SpotBalance:
    return SpotBalance(asset=raw["asset"], free=_dec(raw["free"]), locked=_dec(raw["locked"]))


def map_futures_balance(raw: dict) -> SpotBalance:
    total = _dec(raw.get("balance", "0"))
    available = _dec(raw.get("availableBalance", "0"))
    locked = max(total - available, Decimal(0))
    return SpotBalance(asset=raw["asset"], free=available, locked=locked)


_MARGIN_ALIASES: dict[str, MarginType] = {
    "CROSS": MarginType.CROSSED, "CROSSED": MarginType.CROSSED,
    "ISOLATED": MarginType.ISOLATED,
}


def _map_margin_type(raw: dict) -> MarginType:
    margin_raw = raw.get("marginType", raw.get("isolated", False))
    if isinstance(margin_raw, bool):
        return MarginType.ISOLATED if margin_raw else MarginType.CROSSED
    return _MARGIN_ALIASES.get(str(margin_raw).strip().upper(), MarginType.CROSSED)


def map_futures_position(raw: dict) -> FuturesPosition:
    amt = _dec(raw.get("positionAmt", "0"))
    explicit = raw.get("positionSide")
    if explicit and explicit != "BOTH":
        side = PositionSide(explicit)
    else:
        side = PositionSide.LONG if amt >= 0 else PositionSide.SHORT

    return FuturesPosition(
        symbol=raw["symbol"], position_side=side, quantity=abs(amt),
        entry_price=_dec(raw.get("entryPrice", "0")),
        mark_price=_dec(raw.get("markPrice", "0")),
        unrealized_pnl=_dec(raw.get("unRealizedProfit", "0")),
        realized_pnl=Decimal(0),
        leverage=int(raw.get("leverage", 1)),
        margin_type=_map_margin_type(raw),
        isolated_margin=_dec(raw.get("isolatedMargin", "0")),
        liquidation_price=(
            _dec(raw["liquidationPrice"])
            if raw.get("liquidationPrice") not in (None, "0", 0) else None
        ),
        updated_at=_dt_ms(raw["updateTime"]) if raw.get("updateTime") else datetime.now(UTC),
    )