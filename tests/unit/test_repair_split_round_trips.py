"""Reparo dos ciclos partidos pelo bug de fills contados em dobro (2026-10-12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.database.models.round_trip import RoundTripORM
from app.database.models.trade import TradeORM
from scripts.repair_split_round_trips import plan_repairs

T0 = datetime(2026, 10, 12, 3, 15, tzinfo=UTC)


def _trade(tid, order, side, qty, price, trip, role, minutes) -> TradeORM:
    return TradeORM(trade_id=tid, order_id=order, symbol="BTCUSDT", market_type="FUTURES",
                    side=side, quantity=Decimal(qty), price=Decimal(price), fee=Decimal("0.1"),
                    fee_asset="USDT", executed_at=T0 + timedelta(minutes=minutes),
                    round_trip_id=trip, role=role)


def _trip(rid, side, status, entry, exit_) -> RoundTripORM:
    return RoundTripORM(round_trip_id=rid, symbol="BTCUSDT", market_type="FUTURES",
                        position_side=side, status=status, entry_quantity=Decimal(entry),
                        entry_avg_price=Decimal(100), entry_fee=Decimal("0.1"),
                        exit_quantity=Decimal(exit_), exit_fee=Decimal(0), opened_at=T0)


def _live_case():
    """Igual ao visto ao vivo: SHORT 10 fechado com 7 contados em dobro; os 3
    restantes da mesma ordem abriram um LONG fantasma, que depois absorveu um
    LONG real (entrada 10 e saída 10)."""
    trips = [_trip("orig", "SHORT", "CLOSED", "10", "10"), _trip("ghost", "LONG", "OPEN", "13", "10")]
    trades = [
        _trade("e1", "E", "SELL", "10", "100", "orig", "ENTRY", 0),
        _trade("x1", "X", "BUY", "7", "90", "orig", "EXIT", 30),
        _trade("x2", "X", "BUY", "3", "90", "ghost", "ENTRY", 30),
        _trade("n1", "N", "BUY", "10", "95", "ghost", "ENTRY", 85),
        _trade("n2", "Z", "SELL", "10", "96", "ghost", "EXIT", 86),
    ]
    return trips, trades


def test_leftover_fills_go_back_and_later_trade_becomes_its_own_trip():
    trips, trades = _live_case()
    (r,) = plan_repairs(trips, trades, {"Z": "tx-abc"})
    assert r.phantom.round_trip_id == "ghost" and r.original.round_trip_id == "orig"
    assert [t.trade_id for t in r.back_to_original] == ["x2"]
    v = r.original_values
    assert v.exit_quantity == Decimal(10) and v.closed
    assert v.gross_pnl == Decimal(100)               # SHORT 100 -> 90, 10 unidades
    assert v.net_pnl == Decimal(100) - Decimal("0.3")  # 1 entrada + 2 saídas de 0,1
    n = r.new_trip_values
    assert n is not None and n.closed and n.entry_quantity == Decimal(10)
    assert n.gross_pnl == Decimal(10)                 # LONG 95 -> 96
    assert r.new_trip_reason == "TIME_EXIT"
    assert [t.trade_id for t in r.new_trip_trades] == ["n1", "n2"]


def test_healthy_trips_are_left_alone():
    trips = [_trip("a", "SHORT", "CLOSED", "10", "10"), _trip("b", "LONG", "OPEN", "5", "0")]
    trades = [
        _trade("e1", "E", "SELL", "10", "100", "a", "ENTRY", 0),
        _trade("x1", "X", "BUY", "10", "90", "a", "EXIT", 30),
        _trade("b1", "B", "BUY", "5", "95", "b", "ENTRY", 40),  # ordem própria: ciclo real
    ]
    assert plan_repairs(trips, trades) == []
