"""Avaliação do modo sombra com regra de parada pré-registrada (Fase 6c).

Regra (registrada em `docs/research/hypotheses.json` antes do T0):

- **Coletando**: menos de 100 trades fechados. Só a contagem; nenhuma estatística.
- **Olhada intermediária**: a partir de 100 trades fechados. Estatística dos
  100 primeiros, **só informativa**: nunca aprova nem encerra o teste.
- **Final**: nos 300 primeiros trades fechados ou em 12 meses desde o T0, o
  que vier primeiro. Mesmo gate do laboratório (≥300 trades, IC 95% > 0,
  ≥4/5 blocos, PF > 1,1, DD ≤ 20%, timing contra o acaso p ≤ 0,05). Teste
  confirmatório de UMA hipótese escolhida antes de os dados existirem: sem
  correção de Bonferroni. Em 12 meses sem 300 trades, o critério de amostra
  falha e a estratégia não é aprovada.

Para a avaliação não depender de quando o relatório é rodado (rodar de novo
até "dar certo" seria escolher o resultado), cada estágio tem um corte fixo:
a saída do 100º / 300º trade fechado (ou T0 + 12 meses). Tudo depois do corte
é ignorado, inclusive velas e funding; posições abertas no corte não contam.
A simulação é refeita dos dados públicos (`run_shadow`), não lida do banco.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.backtest.benchmarks import buy_and_hold
from app.backtest.lab import LabReport, RunSummary, assess
from app.backtest.shadow import ShadowSpec, run_shadow
from app.backtest.shadow_runner import (
    CandleLoader,
    FundingLoader,
    default_funding_loader,
    default_loader,
)
from app.backtest.stats import summarize
from app.backtest.types import BacktestTrade
from app.domain.models.market import Candle

INTERIM_TRADES = 100
FINAL_TRADES = 300
MAX_DAYS = 365

COLLECTING = "COLETANDO"
INTERIM = "OLHADA_INTERMEDIARIA"
FINAL = "FINAL"


class ShadowStage(BaseModel):
    stage: str
    closed_trades: int  # fechados até agora (sem corte)
    cutoff: datetime | None = None
    reason: str = ""


class ShadowEvaluation(BaseModel):
    hypothesis: str
    start: datetime
    generated_at: datetime
    stage: ShadowStage
    report: LabReport | None = None


def stage_for(exit_times: list[datetime], *, start: datetime, now: datetime) -> ShadowStage:
    """Estágio e corte pela regra pré-registrada."""
    exits = sorted(exit_times)
    deadline = start + timedelta(days=MAX_DAYS)
    n = len(exits)
    if n >= FINAL_TRADES and exits[FINAL_TRADES - 1] <= deadline:
        return ShadowStage(stage=FINAL, closed_trades=n, cutoff=exits[FINAL_TRADES - 1],
                           reason=f"{FINAL_TRADES} trades fechados")
    if now >= deadline:
        return ShadowStage(stage=FINAL, closed_trades=n, cutoff=deadline,
                           reason=f"{MAX_DAYS} dias desde o início")
    if n >= INTERIM_TRADES:
        return ShadowStage(stage=INTERIM, closed_trades=n, cutoff=exits[INTERIM_TRADES - 1],
                           reason=f"{INTERIM_TRADES} trades fechados (só informativa)")
    return ShadowStage(stage=COLLECTING, closed_trades=n,
                       reason=f"{n} de {INTERIM_TRADES} trades para a olhada intermediária")


def _closed(trades: list[BacktestTrade]) -> list[BacktestTrade]:
    return [t for t in trades if t.exit_reason != "end"]  # "end" = ainda aberta


async def evaluate_shadow(
    spec: ShadowSpec,
    *,
    now: datetime | None = None,
    loader: CandleLoader | None = None,
    funding_loader: FundingLoader | None = None,
) -> ShadowEvaluation:
    now = now or datetime.now(UTC)
    load = loader or default_loader
    load_funding = funding_loader or default_funding_loader
    funding_days = max(spec.history_days(i, now) for i in spec.lab.intervals)

    streams: list[tuple[str, str, list[Candle], list[tuple[int, float]] | None]] = []
    funding: dict[str, list[tuple[int, float]]] = {}
    for interval in spec.lab.intervals:
        for symbol in spec.lab.symbols:
            candles = await load(symbol, interval, spec.history_days(interval, now))
            rates = None
            if spec.lab.funding:
                if symbol not in funding:
                    funding[symbol] = await load_funding(symbol, funding_days)
                rates = funding[symbol]
            streams.append((symbol, interval, candles, rates))

    exits = [
        t.exit_time
        for symbol, interval, candles, rates in streams
        for t in _closed(run_shadow(spec, symbol=symbol, interval=interval, candles=candles, funding=rates).trades)
        if t.exit_time is not None
    ]
    stage = stage_for(exits, start=spec.start, now=now)
    evaluation = ShadowEvaluation(hypothesis=spec.hypothesis, start=spec.start, generated_at=now, stage=stage)
    if stage.cutoff is None:
        return evaluation

    cutoff = stage.cutoff
    cutoff_ms = int(cutoff.timestamp() * 1000)
    runs: list[RunSummary] = []
    trades: list[BacktestTrade] = []
    timing_runs: list[tuple[list[Candle], list[BacktestTrade]]] = []
    for symbol, interval, candles, rates in streams:
        upto = [c for c in candles if c.close_time <= cutoff]
        rates_upto = None if rates is None else [(t, r) for t, r in rates if t <= cutoff_ms]
        run = run_shadow(spec, symbol=symbol, interval=interval, candles=upto, funding=rates_upto)
        closed = _closed(run.trades)
        tested = [c for c in upto if c.open_time >= spec.start]  # período do teste, sem aquecimento
        if not tested:
            continue
        trades.extend(closed)
        timing_runs.append((tested, closed))
        runs.append(RunSummary(
            symbol=symbol,
            interval=interval,
            candles=len(tested),
            max_drawdown_pct=run.max_drawdown_pct,
            stats=summarize(closed),
            total_return_pct=run.total_return_pct,
            exposure_pct=run.exposure_pct,
            max_dd_duration_days=run.max_dd_duration_days,
            buy_hold=buy_and_hold(tested),
        ))
    if not runs:
        return evaluation
    config = spec.lab.model_copy(update={
        "tested_hypotheses": 1,  # confirmatório, pré-registrado: sem Bonferroni
        "until": cutoff,
        "days": (cutoff - spec.start).total_seconds() / 86_400,
    })
    evaluation.report = assess(config, runs=runs, trades=trades, timing_runs=timing_runs,
                               period_start=spec.start, period_end=cutoff)
    return evaluation


def header_markdown(e: ShadowEvaluation) -> str:
    s = e.stage
    lines = [
        f"# Modo sombra — {e.hypothesis}",
        "",
        f"- Início (T0): {e.start:%Y-%m-%d %H:%M} UTC | gerado em {e.generated_at:%Y-%m-%d %H:%M} UTC",
        f"- Trades fechados até agora: {s.closed_trades}",
        f"- **Estágio: {s.stage}** ({s.reason})",
    ]
    if s.cutoff is not None:
        lines.append(f"- Corte da avaliação: {s.cutoff:%Y-%m-%d %H:%M} UTC (nada depois dele entra)")
    if s.stage == INTERIM:
        lines += ["", (
            "> **Olhada intermediária: só informativa, não é veredito.** O gate exige "
            f"{FINAL_TRADES} trades; o \"REPROVADA\" abaixo é por amostra e não encerra o teste."
        )]
    elif s.stage == FINAL:
        lines += ["", "> **Avaliação final pré-registrada.** O veredito abaixo é o oficial do teste para a frente."]
    else:
        lines += ["", "> Ainda coletando: sem estatística até a olhada intermediária."]
    return "\n".join(lines) + "\n"
