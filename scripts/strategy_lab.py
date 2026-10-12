"""Laboratório de estratégias: backtest com custos, walk-forward, IC 95%,
regimes e veredito do gate de promoção.

Os dados vêm da API pública da Binance Futures (dados reais, sem chave) e
ficam em cache em data/klines/. O relatório é salvo em artifacts/lab/.

Uso (PowerShell):
    python scripts/strategy_lab.py --strategy momentum
    python scripts/strategy_lab.py --strategy momentum --intervals 5m,15m,1h --days 180
    python scripts/strategy_lab.py --strategy momentum --hold-bars 6 --fee maker
    python scripts/strategy_lab.py --strategy momentum --param stop_atr_mult=2 --param target_atr_mult=4
    python scripts/strategy_lab.py --strategy trend_breakout --intervals 4h,1d --days 1750 \
        --trailing-stop --funding --symbols BTCUSDT,ETHUSDT,SOLUSDT
    python scripts/strategy_lab.py --strategy xs_momentum --days 1750 --funding \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,ADAUSDT --param top_k=1

Sensibilidade (Fase 4b): `--sweep chave=v1,v2` (repetível) roda a grade e
marca a configuração declarada; não dá veredito. Hipótese oficial:
`--declared L5b-minha-hipotese` grava o veredito em docs/research/hypotheses.json
(versionado). Toda execução vai para artifacts/lab/registry.jsonl.

`xs_momentum` é uma estratégia de carteira (momentum entre ativos): usa
sempre candles 1d e ignora --intervals, --hold-bars, --trailing-stop e
--notional-cap. Parâmetros: lookback_days, top_k, rebalance_days.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.backtest import portfolio_lab, sweep
from app.backtest.lab import STRATEGIES, LabConfig, run_lab, to_markdown
from app.backtest.portfolio import XSMomentumParams
from app.backtest.registry import (
    DeclaredHypothesis,
    append_declared,
    git_commit,
    load_declared,
    record_run,
)

# (entrada, saída) em bps. Maker: ordem limite na entrada; a saída (SL/TP/tempo)
# é sempre a mercado, então taker.
FEE_PRESETS = {"taker": (5.0, 5.0), "maker": (2.0, 5.0)}
REPORT_DIR = Path("artifacts/lab")


def _fees(value: str) -> tuple[float, float]:
    if value in FEE_PRESETS:
        return FEE_PRESETS[value]
    try:
        bps = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use taker, maker ou um número de bps") from exc
    return (bps, bps)


def _param(value: str) -> tuple[str, float]:
    key, sep, raw = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("use chave=valor, ex.: stop_atr_mult=2")
    try:
        return key.strip(), float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"valor numérico inválido em {value!r}") from exc


def _grid(value: str) -> tuple[str, list[float]]:
    key, sep, raw = value.partition("=")
    try:
        values = [float(v) for v in raw.split(",") if v.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"valores numéricos inválidos em {value!r}") from exc
    if not sep or not values:
        raise argparse.ArgumentTypeError("use chave=v1,v2, ex.: channel_period=15,20,30")
    return key.strip(), values


def parse_args(argv: list[str] | None = None) -> LabConfig | portfolio_lab.PortfolioLabConfig:
    return parse_cli(argv)[0]


def parse_cli(
    argv: list[str] | None = None,
) -> tuple[LabConfig | portfolio_lab.PortfolioLabConfig, dict[str, list[float]], str | None]:
    """(configuração, grade de --sweep, id de --declared)."""
    p = argparse.ArgumentParser(description="Laboratório de estratégias")
    p.add_argument(
        "--strategy",
        choices=sorted(set(STRATEGIES) | portfolio_lab.PORTFOLIO_STRATEGIES),
        default="momentum",
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    p.add_argument("--intervals", default="5m")
    p.add_argument("--days", type=float, default=180)
    p.add_argument("--fee", type=_fees, default=FEE_PRESETS["taker"],
                   help="taker (5/5 bps), maker (2/5 bps) ou bps por lado")
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--hold-bars", type=int, default=0, help="saída por tempo em barras (0 = não)")
    p.add_argument("--trailing-stop", action="store_true",
                   help="stop móvel mantendo a distância inicial (estratégias de tendência)")
    p.add_argument("--funding", action="store_true",
                   help="desconta o funding real liquidado durante cada trade")
    p.add_argument("--notional-cap", type=float, default=2000.0)
    p.add_argument("--param", type=_param, action="append", default=[],
                   help="parâmetro da estratégia (repetível), ex.: stop_atr_mult=2")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--sweep", type=_grid, action="append", default=[],
                   help="grade de sensibilidade (repetível), ex.: channel_period=15,20,30")
    p.add_argument("--declared", default=None,
                   help="id da hipótese declarada: grava o veredito em docs/research/hypotheses.json")
    a = p.parse_args(argv)
    grid = dict(a.sweep)
    if grid and a.declared:
        p.error("--sweep é exploratório e não pode ser uma hipótese declarada")
    if grid and a.strategy in portfolio_lab.PORTFOLIO_STRATEGIES:
        p.error("--sweep ainda não suporta estratégias de carteira")
    if a.strategy in portfolio_lab.PORTFOLIO_STRATEGIES:
        try:
            params = XSMomentumParams(**{k: int(v) for k, v in a.param})
        except ValueError as exc:
            p.error(f"parâmetros inválidos para {a.strategy}: {exc}")
        return portfolio_lab.PortfolioLabConfig(
            strategy=a.strategy,
            params=params,
            symbols=[s.strip().upper() for s in a.symbols.split(",") if s.strip()],
            days=a.days,
            # Rebalanceamento a mercado nos dois sentidos: vale a maior corretagem.
            fee_bps=max(a.fee),
            slippage_bps=a.slippage_bps,
            funding=a.funding,
            folds=a.folds,
            seed=a.seed,
        ), grid, a.declared
    tested = len(load_declared())
    if not a.declared or a.declared not in {h.id for h in load_declared()}:
        tested += 1  # esta execução também é um teste
    return LabConfig(
        strategy=a.strategy,
        params=dict(a.param),
        symbols=[s.strip().upper() for s in a.symbols.split(",") if s.strip()],
        intervals=[i.strip() for i in a.intervals.split(",") if i.strip()],
        days=a.days,
        entry_fee_bps=a.fee[0],
        exit_fee_bps=a.fee[1],
        slippage_bps=a.slippage_bps,
        hold_bars=a.hold_bars,
        trailing_stop=a.trailing_stop,
        funding=a.funding,
        max_position_notional=a.notional_cap,
        folds=a.folds,
        seed=a.seed,
        tested_hypotheses=tested,
    ), grid, a.declared


def _universe(config) -> str:
    intervals = ", ".join(getattr(config, "intervals", None) or ["1d"])
    return f"{', '.join(config.symbols)} | {intervals} | {config.days:g} dias"


def main(argv: list[str] | None = None) -> Path:
    logging.basicConfig(level=logging.WARNING)
    config, grid, declared = parse_cli(argv)
    commit = git_commit()  # o código que vai rodar, antes de qualquer commit no meio
    report: BaseModel
    verdict = net = ci_low = timing_p = None
    if grid:
        assert isinstance(config, LabConfig)
        sweep_report = asyncio.run(sweep.run_sweep(config, grid))
        report, md, suffix = sweep_report, sweep.to_markdown(sweep_report), "_sweep"
    elif isinstance(config, portfolio_lab.PortfolioLabConfig):
        portfolio_report = asyncio.run(portfolio_lab.run_portfolio_lab(config))
        report, md, suffix = portfolio_report, portfolio_lab.to_markdown(portfolio_report), ""
        verdict = portfolio_report.verdict.approved
        net, ci_low = portfolio_report.overall.avg_net_pct, portfolio_report.ci95_net_pct[0]
    else:
        lab_report = asyncio.run(run_lab(config))
        report, md, suffix = lab_report, to_markdown(lab_report), ""
        verdict = lab_report.verdict.approved
        net, ci_low = lab_report.overall.avg_net_pct, lab_report.ci95_net_pct[0]
        timing_p = lab_report.timing.p_value if lab_report.timing.trades else None
    print(md)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = REPORT_DIR / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{config.strategy}{suffix}"
    stem.with_suffix(".md").write_text(md, encoding="utf-8")
    stem.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nRelatório salvo em {stem}.md / .json")
    record_run(
        "sweep" if grid else "lab",
        {"report": str(stem), "strategy": config.strategy, "declared": declared,
         "config": config.model_dump(mode="json"), "grid": grid or None,
         "approved": verdict, "net_pct": net, "ci95_low": ci_low, "timing_p": timing_p},
        commit=commit,
    )
    if declared:
        n = append_declared(DeclaredHypothesis(
            id=declared,
            date=f"{datetime.now(UTC):%Y-%m-%d}",
            strategy=config.strategy,
            params=dict(config.params) if isinstance(config, LabConfig) else config.params.model_dump(),
            universe=_universe(config),
            verdict="APROVADA" if verdict else "REPROVADA",
            net_pct=net,
            ci95_low=ci_low,
            timing_p=timing_p,
            report=str(stem),
        ))
        print(f"Hipótese {declared!r} registrada ({n} declaradas): versione docs/research/hypotheses.json.")
    return stem


if __name__ == "__main__":
    main()
