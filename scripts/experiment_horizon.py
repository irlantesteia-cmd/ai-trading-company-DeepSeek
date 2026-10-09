"""Spike ML: varredura de intervalos × horizontes para buscar edge.

Standalone — não toca DB nem .env. Fala com Binance via httpx direto
(REST público, sem auth) para poder paginar acima do cap de 1500 candles
da API, sem alterar o adapter de produção.

ML-3d-2: compara o pipeline OHLCV (8 features) com o mesmo pipeline +
`taker_buy_ratio` (9 features). Produção continua em 8 features até esta
medição ser estável (`n_train >= 5000`, preferir `--limit 10000` ou mais).

`--real` aponta para fapi.binance.com (dados reais, história profunda) em
vez de demo-fapi.binance.com.

Uso (PowerShell):
    python scripts/experiment_horizon.py
    python scripts/experiment_horizon.py --intervals 5m --horizons 1,2,3,5 --limit 5000
    python scripts/experiment_horizon.py --real --intervals 5m --horizons 1 --limit 10000
    python scripts/experiment_horizon.py --real --features both --intervals 5m --horizons 1,5 --limit 10000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.exchanges.binance.mappers import map_kline
from app.features.pipeline import FeaturePipeline, default_pipeline
from app.ml.dataset import build_dataset
from app.ml.training import train_walk_forward

logger = logging.getLogger(__name__)

_DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
_DEFAULT_INTERVALS = ["5m", "15m", "30m", "1h", "4h"]
_DEFAULT_HORIZONS = [1, 3, 5]
_DEFAULT_LIMIT = 1500
_MARKET_TYPE = MarketType.FUTURES
_BINANCE_PAGE_SIZE = 1500  # cap duro de /fapi/v1/klines
_HTTP_TIMEOUT_S = 30.0
_HTTP_MAX_RETRIES = 3
_FEATURE_CHOICES = ("ohlcv", "taker_buy", "both")


def _base_url(real: bool) -> str:
    """Base REST da Binance. `real=True` ignora o setting de testnet."""
    if real:
        return "https://fapi.binance.com"
    if settings.binance_testnet:
        return "https://demo-fapi.binance.com"
    return "https://fapi.binance.com"


@dataclass(frozen=True)
class Row:
    interval: str
    horizon: int
    symbol: str
    pipeline: str
    n_features: int
    n_train: int
    n_test: int
    wf_mean: float | None
    wf_std: float | None
    wf_min: float | None
    deployable: bool


async def _fetch_page(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    symbol: str,
    interval: str,
    limit: int,
    end_time_ms: int | None,
) -> list[list]:
    """Uma chamada a /fapi/v1/klines com retry exponencial."""
    params: dict[str, object] = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    if end_time_ms is not None:
        params["endTime"] = end_time_ms

    last_exc: Exception | None = None
    for attempt in range(_HTTP_MAX_RETRIES):
        try:
            r = await client.get(f"{base_url}/fapi/v1/klines", params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning(
                "experiment.fetch_retry",
                extra={
                    "symbol": symbol,
                    "interval": interval,
                    "attempt": attempt + 1,
                    "error": str(exc),
                },
            )
            await asyncio.sleep(1.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def _fetch_candles(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    symbol: str,
    interval: str,
    total_limit: int,
) -> list[Candle]:
    """Pagina para trás até juntar `total_limit` candles (ascendente)."""
    collected: list[Candle] = []
    end_time_ms: int | None = None
    while len(collected) < total_limit:
        remaining = total_limit - len(collected)
        page_size = min(_BINANCE_PAGE_SIZE, remaining)
        raw = await _fetch_page(
            client,
            base_url=base_url,
            symbol=symbol,
            interval=interval,
            limit=page_size,
            end_time_ms=end_time_ms,
        )
        if not raw:
            break

        page = [map_kline(row, _MARKET_TYPE, symbol, interval) for row in raw]
        collected = page + collected  # página antiga vem antes
        oldest_open_ms = int(raw[0][0])
        end_time_ms = oldest_open_ms - 1

        if len(raw) < page_size:
            break

    # De-dup por open_time (defesa contra borda do endTime) e ordena.
    seen: set = set()
    deduped: list[Candle] = []
    for c in collected:
        if c.open_time in seen:
            continue
        seen.add(c.open_time)
        deduped.append(c)
    deduped.sort(key=lambda c: c.open_time)
    return deduped[-total_limit:]


def _pipeline_for(kind: str) -> FeaturePipeline:
    if kind == "taker_buy":
        return default_pipeline(include_taker_buy=True)
    return default_pipeline()


def _train_one(
    candles: list[Candle],
    *,
    symbol: str,
    interval: str,
    horizon: int,
    pipeline_kind: str,
) -> Row | None:
    pipeline = _pipeline_for(pipeline_kind)
    dataset = build_dataset(
        candles,
        pipeline=pipeline,
        horizon=horizon,
        min_return_pct=settings.ml_label_min_return_pct,
    )
    if dataset.X.shape[0] < settings.ml_walk_forward_min_train * 2:
        return None

    result = train_walk_forward(
        dataset,
        symbol=symbol,
        n_folds=settings.ml_walk_forward_folds,
        min_train_size=settings.ml_walk_forward_min_train,
        random_state=42,
        C=1.0,
        min_deploy_auc=settings.ml_min_deploy_auc,
        max_std=settings.ml_walk_forward_max_std,
    )
    m = result.test_metrics
    return Row(
        interval=interval,
        horizon=horizon,
        symbol=symbol,
        pipeline=pipeline_kind,
        n_features=len(pipeline.names),
        n_train=result.metadata.n_train,
        n_test=result.metadata.n_test,
        wf_mean=m.get("wf_mean_auc"),
        wf_std=m.get("wf_std_auc"),
        wf_min=m.get("wf_min_auc"),
        deployable=result.metadata.deployable,
    )


def _print_table(
    rows: list[Row],
    *,
    intervals: list[str],
    horizons: list[int],
) -> None:
    if not rows:
        print("(nenhum resultado)")
        return

    kinds = []
    for r in rows:
        if r.pipeline not in kinds:
            kinds.append(r.pipeline)

    by_key: dict[tuple[str, str, str], dict[int, Row]] = {}
    for r in rows:
        by_key.setdefault((r.pipeline, r.interval, r.symbol), {})[r.horizon] = r

    labels = [f"h={h}" for h in horizons]
    for kind in kinds:
        n_feat = next((r.n_features for r in rows if r.pipeline == kind), 0)
        for interval in intervals:
            symbols = [
                s
                for s in {r.symbol for r in rows if r.pipeline == kind}
                if any(
                    r.interval == interval and r.symbol == s and r.pipeline == kind
                    for r in rows
                )
            ]
            if not symbols:
                continue
            print(f"\n=== interval={interval} pipeline={kind} ({n_feat}f) ===")
            header = f"{'symbol':<9}" + "".join(f"  {lbl:>8}" for lbl in labels)
            print(header)
            print("-" * len(header))
            for symbol in symbols:
                line = f"{symbol:<9}"
                for h in horizons:
                    r = by_key.get((kind, interval, symbol), {}).get(h)
                    if r is None or r.wf_mean is None:
                        line += f"  {'n/a':>8}"
                    else:
                        line += f"  {r.wf_mean:>8.4f}"
                print(line)

    if "ohlcv" in kinds and "taker_buy" in kinds:
        print("\n=== delta taker_buy - ohlcv (wf_mean) ===")
        header = f"{'interval':<8} {'symbol':<9}" + "".join(
            f"  {lbl:>8}" for lbl in labels
        )
        print(header)
        print("-" * len(header))
        keys = sorted({(r.interval, r.symbol) for r in rows})
        for interval, symbol in keys:
            line = f"{interval:<8} {symbol:<9}"
            for h in horizons:
                base = by_key.get(("ohlcv", interval, symbol), {}).get(h)
                micro = by_key.get(("taker_buy", interval, symbol), {}).get(h)
                if (
                    base is None
                    or micro is None
                    or base.wf_mean is None
                    or micro.wf_mean is None
                ):
                    line += f"  {'n/a':>8}"
                else:
                    line += f"  {micro.wf_mean - base.wf_mean:>+8.4f}"
            print(line)

    flagged = [r for r in rows if r.wf_mean is not None and r.wf_mean >= 0.58]
    if flagged:
        print("\n=== candidatos (wf_mean >= 0.58) — checagem de estabilidade ===")
        hdr = (
            f"{'interval':<8} {'symbol':<9} {'pipe':<10} {'h':>3} {'mean':>7} "
            f"{'std':>7} {'min':>7} {'n_train':>8} {'feat':>4} {'deploy':>7}"
        )
        print(hdr)
        print("-" * len(hdr))
        for r in sorted(flagged, key=lambda x: (-(x.wf_mean or 0),)):
            print(
                f"{r.interval:<8} {r.symbol:<9} {r.pipeline:<10} {r.horizon:>3} "
                f"{(r.wf_mean or 0):>7.4f} "
                f"{(r.wf_std or 0):>7.4f} "
                f"{(r.wf_min or 0):>7.4f} "
                f"{r.n_train:>8} {r.n_features:>4} {r.deployable!s:>7}"
            )


def _pipeline_kinds(features: str) -> list[str]:
    if features == "both":
        return ["ohlcv", "taker_buy"]
    return [features]


async def _run(
    *,
    symbols: list[str],
    intervals: list[str],
    horizons: list[int],
    limit: int,
    real: bool,
    features: str,
) -> None:
    base_url = _base_url(real)
    kinds = _pipeline_kinds(features)
    logger.info(
        "experiment.base_url",
        extra={
            "url": base_url,
            "limit": limit,
            "real": real,
            "features": kinds,
        },
    )

    rows: list[Row] = []
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        for interval in intervals:
            for symbol in symbols:
                try:
                    candles = await _fetch_candles(
                        client,
                        base_url=base_url,
                        symbol=symbol,
                        interval=interval,
                        total_limit=limit,
                    )
                except Exception:
                    logger.exception(
                        "experiment.fetch_failed",
                        extra={"symbol": symbol, "interval": interval},
                    )
                    continue
                if len(candles) < 200:
                    logger.warning(
                        "experiment.insufficient_candles",
                        extra={
                            "symbol": symbol,
                            "interval": interval,
                            "n": len(candles),
                        },
                    )
                    continue

                n_taker = sum(1 for c in candles if c.taker_buy_base_volume is not None)
                logger.info(
                    "experiment.candles_ready",
                    extra={
                        "symbol": symbol,
                        "interval": interval,
                        "n": len(candles),
                        "n_taker_buy": n_taker,
                        "first": candles[0].open_time.isoformat(),
                        "last": candles[-1].open_time.isoformat(),
                    },
                )

                for horizon in horizons:
                    for kind in kinds:
                        try:
                            row = _train_one(
                                candles,
                                symbol=symbol,
                                interval=interval,
                                horizon=horizon,
                                pipeline_kind=kind,
                            )
                        except Exception:
                            logger.exception(
                                "experiment.train_failed",
                                extra={
                                    "symbol": symbol,
                                    "interval": interval,
                                    "horizon": horizon,
                                    "pipeline": kind,
                                },
                            )
                            continue
                        if row is None:
                            logger.warning(
                                "experiment.insufficient_samples",
                                extra={
                                    "symbol": symbol,
                                    "interval": interval,
                                    "horizon": horizon,
                                    "pipeline": kind,
                                },
                            )
                            continue
                        rows.append(row)

    _print_table(rows, intervals=intervals, horizons=horizons)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Varredura de intervalos × horizontes para ML (spike)."
    )
    p.add_argument(
        "--symbols",
        default=",".join(_DEFAULT_SYMBOLS),
        help="símbolos separados por vírgula (default: BTC,ETH,SOL,XRP)",
    )
    p.add_argument(
        "--intervals",
        default=",".join(_DEFAULT_INTERVALS),
        help="intervalos separados por vírgula (default: 5m,15m,30m,1h,4h)",
    )
    p.add_argument(
        "--horizons",
        default=",".join(map(str, _DEFAULT_HORIZONS)),
        help="horizontes em candles à frente (default: 1,3,5)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=(
            "candles por série (default: 1500). Valores acima de 1500 são "
            "obtidos via paginação automática. Medição séria: >= 10000."
        ),
    )
    p.add_argument(
        "--features",
        choices=_FEATURE_CHOICES,
        default="both",
        help=(
            "ohlcv=8 features, taker_buy=9 (inclui taker_buy_ratio), "
            "both=compara os dois (default)."
        ),
    )
    p.add_argument(
        "--real",
        action="store_true",
        help=(
            "usa fapi.binance.com (dados reais, história profunda) em vez de "
            "demo-fapi.binance.com. Klines são públicos; não requer chave."
        ),
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    asyncio.run(
        _run(
            symbols=symbols,
            intervals=intervals,
            horizons=horizons,
            limit=args.limit,
            real=args.real,
            features=args.features,
        )
    )


if __name__ == "__main__":
    main()
