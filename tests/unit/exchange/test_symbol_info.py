from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import MarketType
from app.domain.models.asset import TradingPair
from app.exchanges.symbol_info import SymbolInfoService


def _pair(
    symbol: str = "SOLUSDT",
    *,
    step: str = "0.01",
    price_precision: int = 2,
) -> TradingPair:
    return TradingPair(
        symbol=symbol,
        base=symbol[:-4],
        quote="USDT",
        market_type=MarketType.FUTURES,
        price_precision=price_precision,
        quantity_precision=2,
        min_notional=Decimal(5),
        min_quantity=Decimal("0.01"),
        step_size=Decimal(step),
        status="TRADING",
    )


def test_put_and_get():
    svc = SymbolInfoService()
    svc.put(_pair("SOLUSDT"))
    assert svc.has("SOLUSDT", MarketType.FUTURES)
    assert svc.get("SOLUSDT", MarketType.FUTURES) is not None
    assert svc.get("BTCUSDT", MarketType.FUTURES) is None
    assert svc.size == 1


def test_round_quantity_sol_step_001():
    svc = SymbolInfoService()
    svc.put(_pair("SOLUSDT", step="0.01"))
    assert svc.round_quantity(
        "SOLUSDT", MarketType.FUTURES, Decimal("85.23695874")
    ) == Decimal("85.23")


def test_round_quantity_xrp_step_1():
    svc = SymbolInfoService()
    svc.put(_pair("XRPUSDT", step="1"))
    assert svc.round_quantity(
        "XRPUSDT", MarketType.FUTURES, Decimal("6727.66415500")
    ) == Decimal(6727)


def test_round_quantity_btc_step_0001():
    svc = SymbolInfoService()
    svc.put(_pair("BTCUSDT", step="0.001"))
    assert svc.round_quantity(
        "BTCUSDT", MarketType.FUTURES, Decimal("0.12345678")
    ) == Decimal("0.123")


def test_round_quantity_without_info_returns_as_is():
    svc = SymbolInfoService()
    result = svc.round_quantity(
        "UNKNOWN", MarketType.FUTURES, Decimal("1.23456789")
    )
    assert result == Decimal("1.23456789")


def test_round_price_sol_precision_2():
    svc = SymbolInfoService()
    svc.put(_pair("SOLUSDT", price_precision=2))
    assert svc.round_price(
        "SOLUSDT", MarketType.FUTURES, Decimal("117.76018254")
    ) == Decimal("117.76")


def test_round_price_btc_precision_1():
    svc = SymbolInfoService()
    svc.put(_pair("BTCUSDT", price_precision=1))
    assert svc.round_price(
        "BTCUSDT", MarketType.FUTURES, Decimal("84306.2789")
    ) == Decimal("84306.2")


@pytest.mark.asyncio
async def test_load_populates_cache():
    svc = SymbolInfoService()
    exchange = MagicMock()
    exchange.list_trading_pairs = AsyncMock(
        return_value=[_pair("SOLUSDT"), _pair("XRPUSDT")]
    )
    n = await svc.load(exchange, market_type=MarketType.FUTURES)
    assert n == 2
    assert svc.size == 2


@pytest.mark.asyncio
async def test_load_handles_failure_gracefully():
    svc = SymbolInfoService()
    exchange = MagicMock()
    exchange.list_trading_pairs = AsyncMock(side_effect=RuntimeError("boom"))
    n = await svc.load(exchange, market_type=MarketType.FUTURES)
    assert n == 0
    assert svc.size == 0