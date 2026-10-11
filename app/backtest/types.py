from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.core.enums import MarketType, OrderSide, SignalDirection
from app.domain.models.base import DomainModel


class BacktestConfig(DomainModel):
    initial_capital: Decimal = Decimal(10000)
    risk_per_trade_pct: float = 0.01
    fee_bps: float = 5.0        # 5 bps = 0,05% por lado (taker na Binance Futures)
    slippage_bps: float = 2.0   # 2 bps
    # Taxas por lado, quando diferentes (ex.: entrada maker 2 bps, saída taker 5).
    # None = usa `fee_bps`.
    entry_fee_bps: float | None = None
    exit_fee_bps: float | None = None
    # Candles passados à estratégia a cada barra (o AssetAgent ao vivo usa 200).
    # None = todo o histórico até a barra (lento: O(n²)).
    lookback: int | None = 200
    # Saída por tempo: fecha no close da N-ésima barra desde a entrada (0 desliga).
    max_holding_bars: int = 0
    # SL/TP reancorados no preço real de entrada (como o ExecutionAgent ao vivo).
    anchor_to_fill: bool = True
    # Teto de notional por posição (como RISK_MAX_POSITION_NOTIONAL). None = sem teto.
    max_position_notional: Decimal | None = None


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
    exit_reason: str | None = None          # "stop" | "target" | "time" | "end"
    pnl: Decimal = Decimal(0)               # líquido (após taxas e derrapagem)
    fees: Decimal = Decimal(0)
    return_pct: float = 0.0                 # líquido, % do notional de entrada
    # Bruto: só o movimento de preço entre os sinais (sem taxas nem derrapagem).
    gross_return_pct: float = 0.0
    bars_held: int = 0

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