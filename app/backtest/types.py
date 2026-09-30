from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.core.enums import MarketType, OrderSide, SignalDirection
from app.domain.models.base import DomainModel


class BacktestConfig(DomainModel):
    initial_capital: Decimal = Decimal(10000)
    risk_per_trade_pct: float = 0.01
    fee_bps: float = 5.0        # 5 bps = 0,05%
    slippage_bps: float = 2.0   # 2 bps


class BacktestTrade(DomainModel):
    signal_id: str
    symbol: str
    market_type: MarketType
    direction: SignalDirection
    side: OrderSide
    entry_time: datetime
    entry_price: Decimal
    quantity: Decimal
    stop_price: Decimal
    target_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: str | None = None          # "stop" | "target" | "end"
    pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    return_pct: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_time is None


class BacktestResult(DomainModel):
    symbol: str
    interval: str
    strategy: str
    initial_capital: Decimal
    final_equity: Decimal
    total_return_pct: float
    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[tuple[datetime, Decimal]] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)