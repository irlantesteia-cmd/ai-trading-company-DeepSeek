"""Modo sombra (Fase 6): teste para a frente de uma estratégia do laboratório.

A cada vela fechada, a simulação é refeita desde uma data de início fixa (T0)
com as MESMAS regras do laboratório (`engine_config`): entrada na abertura da
barra seguinte, stop móvel, taxas, funding real e filtro de volatilidade.
Nenhuma ordem é enviada; o resultado é só registrado.

Por que refazer em vez de guardar estado: com o início do histórico e o T0
fixos, a simulação é determinística. Reiniciar o serviço, perder um ciclo ou
recalcular devolve os mesmos trades, e os trades já fechados não mudam quando
chegam velas novas. O histórico antes do T0 só aquece indicadores e o
classificador de volatilidade (que precisa de centenas de barras); nenhuma
entrada acontece antes do T0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.backtest.engine import BacktestEngine
from app.backtest.lab import LabConfig, build_strategy, engine_config
from app.backtest.regime import RegimeParams, classify_regimes
from app.backtest.registry import DeclaredHypothesis
from app.backtest.types import BacktestTrade
from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.market.validation import interval_ms

OPEN = "OPEN"
CLOSED = "CLOSED"

# Universo e regras do L6b (relatório 20261013T150922Z_trend_breakout).
L6_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT",
    "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT",
]


def l6_lab_config() -> LabConfig:
    """Configuração declarada do L6 (tendência com filtro de volatilidade)."""
    return LabConfig(
        strategy="trend_breakout",
        symbols=list(L6_SYMBOLS),
        intervals=["4h", "1d"],
        trailing_stop=True,
        funding=True,
        skip_vol=["HIGH"],
    )


class ShadowSpec(BaseModel):
    hypothesis: str
    start: datetime  # T0: primeira barra em que uma entrada pode acontecer
    lab: LabConfig = Field(default_factory=l6_lab_config)
    # Barras de aquecimento antes do T0. O classificador de volatilidade só
    # rotula com metade da janela (`vol_lookback`) e estabiliza com ela cheia.
    warmup_bars: int = RegimeParams().vol_lookback + 100

    def history_start(self, interval: str) -> datetime:
        """Início fixo do histórico de um intervalo (T0 − aquecimento)."""
        step = interval_ms(interval)
        if step is None:
            raise ValueError(f"intervalo desconhecido: {interval!r}")
        return self.start - timedelta(milliseconds=step * self.warmup_bars)

    def history_days(self, interval: str, now: datetime) -> float:
        """Dias a carregar até `now` para cobrir o histórico inteiro (+1 de folga)."""
        return (now - self.history_start(interval)).total_seconds() / 86_400 + 1


def spec_from_registry(hypothesis: str, declared: list[DeclaredHypothesis]) -> ShadowSpec | None:
    """Spec do modo sombra a partir do registro de hipóteses.

    None se a hipótese ainda não foi registrada (o serviço espera). O T0
    precisa ser posterior ao registro: um início no passado deixaria escolher
    o começo olhando o resultado."""
    entry = next((e for e in declared if e.id == hypothesis), None)
    if entry is None or entry.shadow_start is None:
        return None
    start = datetime.fromisoformat(entry.shadow_start)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    registered = datetime.fromisoformat(entry.date).replace(tzinfo=UTC)
    if start < registered:
        raise ValueError(
            f"{hypothesis}: início do modo sombra ({entry.shadow_start}) antes do registro ({entry.date})"
        )
    lab = l6_lab_config()
    if entry.strategy != lab.strategy or entry.params != lab.params:
        raise ValueError(f"{hypothesis}: estratégia/parâmetros diferentes da configuração L6")
    return ShadowSpec(hypothesis=hypothesis, start=start, lab=lab)


class ShadowRun(BaseModel):
    symbol: str
    interval: str
    candles: int
    warmup_candles: int  # barras antes do T0 (aquecimento de fato disponível)
    last_close_time: datetime | None
    last_close: Decimal | None
    entries_skipped_vol: int
    trades: list[BacktestTrade]


def run_shadow(
    spec: ShadowSpec,
    *,
    symbol: str,
    interval: str,
    candles: list[Candle],
    funding: list[tuple[int, float]] | None = None,
) -> ShadowRun:
    """Simula um ativo × intervalo desde o T0. Função pura: o mesmo histórico
    devolve sempre os mesmos trades. O último trade com saída `end` é a
    posição ainda aberta, marcada no último fechamento."""
    since = spec.history_start(interval)
    window = [c for c in candles if c.open_time >= since]
    warmup = sum(1 for c in window if c.open_time < spec.start)
    if not window:
        return ShadowRun(symbol=symbol, interval=interval, candles=0, warmup_candles=0,
                         last_close_time=None, last_close=None, entries_skipped_vol=0, trades=[])
    config = engine_config(spec.lab).model_copy(update={"first_entry_time": spec.start})
    result = BacktestEngine(config).run(
        symbol=symbol,
        interval=interval,
        market_type=MarketType.FUTURES,
        candles=window,
        strategy=build_strategy(spec.lab.strategy, spec.lab.params),
        regimes=classify_regimes(window),
        funding_rates=funding if spec.lab.funding else None,
    )
    return ShadowRun(
        symbol=symbol,
        interval=interval,
        candles=len(window),
        warmup_candles=warmup,
        last_close_time=window[-1].close_time,
        last_close=window[-1].close,
        entries_skipped_vol=int(result.metrics.get("entries_skipped_vol", 0)),
        trades=result.trades,
    )


def shadow_rows(spec: ShadowSpec, run: ShadowRun) -> list[dict[str, Any]]:
    """Linhas para `shadow_trades`, chave (hipótese, ativo, intervalo, entrada).

    Posição aberta: status OPEN, sem saída; `mark_price` e os retornos são os
    de um fechamento no último preço (com taxa de saída: conservador)."""
    rows: list[dict[str, Any]] = []
    for t in run.trades:
        is_open = t.exit_reason == "end"
        rows.append({
            "hypothesis": spec.hypothesis,
            "symbol": run.symbol,
            "interval": run.interval,
            "direction": t.direction.value,
            "status": OPEN if is_open else CLOSED,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "quantity": t.quantity,
            "stop_price": t.stop_price,
            "exit_time": None if is_open else t.exit_time,
            "exit_price": None if is_open else t.exit_price,
            "exit_reason": None if is_open else t.exit_reason,
            "mark_price": run.last_close if is_open else None,
            "pnl": t.pnl,
            "fees": t.fees,
            "funding": t.funding,
            "return_pct": Decimal(str(t.return_pct)),
            "gross_return_pct": Decimal(str(t.gross_return_pct)),
            "funding_pct": Decimal(str(t.funding_pct)),
            "bars_held": t.bars_held,
            "regime_trend": t.regime_trend,
            "regime_vol": t.regime_vol,
        })
    return rows
