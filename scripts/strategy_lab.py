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
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.backtest.lab import STRATEGIES, LabConfig, run_lab, to_markdown

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


def parse_args(argv: list[str] | None = None) -> LabConfig:
    p = argparse.ArgumentParser(description="Laboratório de estratégias")
    p.add_argument("--strategy", choices=sorted(STRATEGIES), default="momentum")
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
    a = p.parse_args(argv)
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
    )


def main(argv: list[str] | None = None) -> Path:
    logging.basicConfig(level=logging.WARNING)
    config = parse_args(argv)
    report = asyncio.run(run_lab(config))
    md = to_markdown(report)
    print(md)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = REPORT_DIR / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{config.strategy}"
    stem.with_suffix(".md").write_text(md, encoding="utf-8")
    stem.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nRelatório salvo em {stem}.md / .json")
    return stem


if __name__ == "__main__":
    main()
