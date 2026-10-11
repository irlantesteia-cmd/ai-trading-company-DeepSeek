"""Backtest de carteira: momentum entre ativos (cross-sectional), comprado e vendido.

Regras (L4):
- Candles diários alinhados por data (UTC). Decisão no fechamento do dia `d`
  quando o dia seguinte cai no calendário de rebalanceamento
  (`rebalance_days`, ancorado na segunda-feira: 7 = toda segunda 00:00 UTC).
- Elegível na decisão: símbolo com candle em `d - lookback_days`, em `d` e em
  `d + 1` (dia da execução). Score = close[d] / close[d - lookback] - 1.
- Compra os `top_k` maiores scores e vende os `top_k` menores, peso igual:
  cada posição = equity / (2·top_k) → exposição bruta 1×, líquida zero. Com
  menos de 2·top_k elegíveis, a carteira fica zerada.
- Execução na abertura de `d + 1`; só a diferença de posição é negociada,
  pagando (corretagem + derrapagem) bps sobre o notional negociado. No fim do
  histórico a carteira é liquidada com o mesmo custo.
- Marcação a mercado na abertura e no fechamento de cada dia. Funding com
  liquidação em (abertura do dia, abertura do dia seguinte] sobre o notional
  na abertura: comprado paga taxa positiva, vendido recebe.

Retornos e equity em float (pesquisa, como os indicadores); nada daqui envia
ordens.
"""

from __future__ import annotations

import bisect
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models.market import Candle

_ANCHOR_MONDAY = date(2018, 1, 1)  # segunda-feira; âncora do calendário
_DAY_MS = 86_400_000


class XSMomentumParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_days: int = 28
    top_k: int = 3
    rebalance_days: int = 7

    @model_validator(mode="after")
    def _check(self) -> XSMomentumParams:
        if self.lookback_days < 1 or self.top_k < 1 or self.rebalance_days < 1:
            raise ValueError("lookback_days, top_k e rebalance_days devem ser >= 1")
        return self


class PortfolioCosts(BaseModel):
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    initial_capital: float = 3000.0


class PortfolioLeg(BaseModel):
    """Uma posição mantida durante um período (entre dois rebalanceamentos)."""

    symbol: str
    side: Literal["LONG", "SHORT"]
    start: datetime
    end: datetime
    return_pct: float  # variação de preço a favor da posição, em %


class PortfolioPeriod(BaseModel):
    """Um período da carteira; percentuais sobre a equity no início."""

    start: datetime
    end: datetime
    equity_start: float
    positions: int
    turnover: float  # notional negociado no rebalanceamento / equity
    long_pct: float = 0.0
    short_pct: float = 0.0
    cost_pct: float = 0.0
    funding_pct: float = 0.0  # + pago, - recebido

    @property
    def gross_pct(self) -> float:
        return self.long_pct + self.short_pct

    @property
    def net_pct(self) -> float:
        return self.gross_pct - self.cost_pct - self.funding_pct


class PortfolioResult(BaseModel):
    periods: list[PortfolioPeriod] = Field(default_factory=list)
    legs: list[PortfolioLeg] = Field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = Field(default_factory=list)
    max_drawdown_pct: float = 0.0


def select_book(scores: dict[str, float], top_k: int) -> dict[str, int]:
    """+1 para os `top_k` maiores scores, -1 para os `top_k` menores.
    Empates desempatam pelo nome (determinístico). Vazio se faltar símbolo."""
    if len(scores) < 2 * top_k:
        return {}
    ranked = sorted(scores, key=lambda s: (scores[s], s))
    book = {s: -1 for s in ranked[:top_k]}
    book.update({s: 1 for s in ranked[-top_k:]})
    return book


def is_rebalance_day(day: date, every_days: int) -> bool:
    return (day - _ANCHOR_MONDAY).days % every_days == 0


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class _Open:
    """Período em andamento: acumula P&L em moeda até fechar."""

    def __init__(self, start: datetime, equity: float, turnover: float, cost: float) -> None:
        self.start = start
        self.equity = equity
        self.turnover = turnover
        self.long = 0.0
        self.short = 0.0
        self.cost = cost
        self.funding = 0.0
        self.entry: dict[str, float] = {}

    def to_period(self, end: datetime, positions: int) -> PortfolioPeriod:
        base = self.equity or 1.0
        return PortfolioPeriod(
            start=self.start,
            end=end,
            equity_start=self.equity,
            positions=positions,
            turnover=self.turnover,
            long_pct=100 * self.long / base,
            short_pct=100 * self.short / base,
            cost_pct=100 * self.cost / base,
            funding_pct=100 * self.funding / base,
        )


def run_xs_momentum(
    candles: dict[str, list[Candle]],
    *,
    params: XSMomentumParams | None = None,
    costs: PortfolioCosts | None = None,
    funding: dict[str, list[tuple[int, float]]] | None = None,
) -> PortfolioResult:
    p = params or XSMomentumParams()
    c = costs or PortfolioCosts()
    rate = (c.fee_bps + c.slippage_bps) / 10_000
    bars = {s: {k.open_time.date(): k for k in cs} for s, cs in candles.items() if cs}
    all_days: set[date] = set()
    for b in bars.values():
        all_days.update(b)
    days = sorted(all_days)
    fund = {s: sorted(v) for s, v in (funding or {}).items()}
    fund_times = {s: [t for t, _ in v] for s, v in fund.items()}

    equity = c.initial_capital
    qty: dict[str, float] = {}
    last: dict[str, float] = {}
    pending: dict[str, int] | None = None
    current: _Open | None = None
    result = PortfolioResult()

    def mark(price_of: dict[str, float]) -> None:
        nonlocal equity
        for s, q in qty.items():
            if s not in price_of:
                continue
            pnl = q * (price_of[s] - last[s])
            last[s] = price_of[s]
            equity += pnl
            if current is not None:
                if q > 0:
                    current.long += pnl
                else:
                    current.short += pnl

    def close_period(end: datetime) -> None:
        if current is None:
            return
        for s, entry in current.entry.items():
            side = 1 if qty[s] > 0 else -1
            result.legs.append(
                PortfolioLeg(
                    symbol=s,
                    side="LONG" if side > 0 else "SHORT",
                    start=current.start,
                    end=end,
                    return_pct=100 * side * (last[s] / entry - 1),
                )
            )
        result.periods.append(current.to_period(end, positions=len(current.entry)))

    for i, day in enumerate(days):
        today = {s: b[day] for s, b in bars.items() if day in b}
        opens = {s: float(k.open) for s, k in today.items()}
        mark(opens)

        if pending is not None and (pending or qty or current is not None):
            open_time = min(k.open_time for k in today.values())
            close_period(open_time)
            book = pending if equity > 0 else {}
            notional = equity / (2 * p.top_k)
            target = {s: side * notional / opens[s] for s, side in book.items()}
            traded = sum(
                abs(target.get(s, 0.0) - qty.get(s, 0.0)) * last.get(s, opens.get(s, 0.0))
                for s in set(target) | set(qty)
            )
            cost = traded * rate
            current = _Open(open_time, equity, traded / equity if equity > 0 else 0.0, cost)
            equity -= cost
            qty = target
            for s in qty:
                last[s] = opens[s]
                current.entry[s] = opens[s]
        pending = None

        if current is not None:
            for s, q in qty.items():
                if s not in today or s not in fund:
                    continue
                lo_ms = _ms(today[s].open_time)
                lo = bisect.bisect_right(fund_times[s], lo_ms)
                hi = bisect.bisect_right(fund_times[s], lo_ms + _DAY_MS)
                paid = q * opens[s] * sum(r for _, r in fund[s][lo:hi])
                current.funding += paid
                equity -= paid

        mark({s: float(k.close) for s, k in today.items()})
        close_time = max(k.close_time for k in today.values())
        result.equity_curve.append((close_time, equity))

        nxt = day + timedelta(days=1)
        if i + 1 < len(days) and is_rebalance_day(nxt, p.rebalance_days):
            past = day - timedelta(days=p.lookback_days)
            scores = {
                s: float(b[day].close) / float(b[past].close) - 1
                for s, b in bars.items()
                if day in b and past in b and nxt in b and b[past].close > 0
            }
            pending = select_book(scores, p.top_k)

    if current is not None and result.equity_curve:
        end_time, _ = result.equity_curve[-1]
        liquidation = sum(abs(q) * last[s] for s, q in qty.items()) * rate
        current.cost += liquidation
        equity -= liquidation
        close_period(end_time)
        result.equity_curve[-1] = (end_time, equity)

    result.max_drawdown_pct = _max_drawdown_pct([e for _, e in result.equity_curve])
    return result


def _max_drawdown_pct(curve: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for e in curve:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, 100 * (peak - e) / peak)
    return worst
