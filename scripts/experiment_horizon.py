"""Spike ML: varredura de intervalos × horizontes para buscar edge.

Standalone — não toca DB nem .env. Fala com Binance via httpx direto
(REST público, sem auth) para poder paginar acima do cap de 1500 candles
da API, sem alterar o adapter de produção.

ML-3d-2: compara o pipeline OHLCV (8 features) com o mesmo pipeline +
`taker_buy_ratio` (9 features). Produção continua em 8 features até esta
medição ser estável (`n_train >= 5000`, preferir `--limit 10000` ou mais).

`--real` aponta para fapi.binance.com (dados reais, história profunda) em
vez de demo-fapi.binance.com.

ML-3e: hipótese "posicionamento do perpétuo antecipa o preço". Duas variantes,
ambas buscadas da API pública e alinhadas sem lookahead:
- `funding`: última taxa liquidada até o fechamento do candle + variação
  entre as duas últimas liquidações (`/fapi/v1/fundingRate`, a cada 8h).
- `premium`: close do premium index do mesmo candle
  (`/fapi/v1/premiumIndexKlines`, base mark−index de onde o funding sai).

Uso (PowerShell):
    python scripts/experiment_horizon.py
    python scripts/experiment_horizon.py --intervals 5m --horizons 1,2,3,5 --limit 5000
    python scripts/experiment_horizon.py --real --intervals 5m --horizons 1 --limit 10000
    python scripts/experiment_horizon.py --real --features both --intervals 5m --horizons 1,5 --limit 10000
    python scripts/experiment_horizon.py --real --features all --intervals 5m,1h --horizons 1,3,5 --limit 10000
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import numpy as np

from app.core.config import settings
from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.exchanges.binance.mappers import map_kline
from app.features.pipeline import FeatureMatrix, FeaturePipeline, default_pipeline
from app.ml.dataset import build_dataset
from app.ml.training import train_walk_forward

logger = logging.getLogger(__name__)

_DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
_DEFAULT_INTERVALS = ["5m", "15m", "30m", "1h", "4h"]
_DEFAULT_HORIZONS = [1, 3, 5]
_DEFAULT_LIMIT = 1500
_MARKET_TYPE = MarketType.FUTURES
_BINANCE_PAGE_SIZE = 1500  # cap duro de /fapi/v1/klines e premiumIndexKlines
_FUNDING_PAGE_SIZE = 1000  # cap duro de /fapi/v1/fundingRate
_FUNDING_LOOKBACK_MS = 2 * 24 * 60 * 60 * 1000  # cobre 2 liquidações antes do 1º candle
_KLINES_PATH = "/fapi/v1/klines"
_PREMIUM_PATH = "/fapi/v1/premiumIndexKlines"
_FUNDING_PATH = "/fapi/v1/fundingRate"
_HTTP_TIMEOUT_S = 30.0
_HTTP_MAX_RETRIES = 3
_FEATURE_CHOICES = ("ohlcv", "taker_buy", "funding", "premium", "both", "all")


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


@dataclass(frozen=True)
class MarketExtras:
    """Séries de mercado do perpétuo usadas pelas variantes ML-3e."""

    # (fundingTime_ms, rate), ascendente por tempo.
    funding: list[tuple[int, float]] = field(default_factory=list)
    # open_time_ms do candle → close do premium index.
    premium_by_open_ms: dict[int, float] = field(default_factory=dict)


def _to_ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, object],
) -> list:
    """GET com retry exponencial."""
    last_exc: Exception | None = None
    for attempt in range(_HTTP_MAX_RETRIES):
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning(
                "experiment.fetch_retry",
                extra={
                    "url": url,
                    "params": params,
                    "attempt": attempt + 1,
                    "error": str(exc),
                },
            )
            await asyncio.sleep(1.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def _fetch_raw_klines(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    path: str,
    symbol: str,
    interval: str,
    total_limit: int,
) -> list[list]:
    """Pagina para trás até juntar `total_limit` klines crus (ascendente).

    Serve para `/fapi/v1/klines` e `/fapi/v1/premiumIndexKlines`, que têm o
    mesmo formato de linha e a mesma paginação por `endTime`.
    """
    collected: list[list] = []
    end_time_ms: int | None = None
    while len(collected) < total_limit:
        remaining = total_limit - len(collected)
        page_size = min(_BINANCE_PAGE_SIZE, remaining)
        params: dict[str, object] = {
            "symbol": symbol,
            "interval": interval,
            "limit": page_size,
        }
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        raw = await _get_json(client, f"{base_url}{path}", params)
        if not raw:
            break

        collected = raw + collected  # página antiga vem antes
        end_time_ms = int(raw[0][0]) - 1

        if len(raw) < page_size:
            break

    # De-dup por open_time (defesa contra borda do endTime) e ordena.
    by_open: dict[int, list] = {}
    for row in collected:
        by_open.setdefault(int(row[0]), row)
    return [by_open[k] for k in sorted(by_open)][-total_limit:]


async def _fetch_candles(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    symbol: str,
    interval: str,
    total_limit: int,
) -> list[Candle]:
    raw = await _fetch_raw_klines(
        client,
        base_url=base_url,
        path=_KLINES_PATH,
        symbol=symbol,
        interval=interval,
        total_limit=total_limit,
    )
    return [map_kline(row, _MARKET_TYPE, symbol, interval) for row in raw]


async def _fetch_premium(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    symbol: str,
    interval: str,
    total_limit: int,
) -> dict[int, float]:
    raw = await _fetch_raw_klines(
        client,
        base_url=base_url,
        path=_PREMIUM_PATH,
        symbol=symbol,
        interval=interval,
        total_limit=total_limit,
    )
    return premium_by_open(raw)


async def _fetch_funding(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, float]]:
    """Pagina para frente por `startTime` em `/fapi/v1/fundingRate`."""
    out: dict[int, float] = {}
    cursor = start_ms
    while cursor <= end_ms:
        raw = await _get_json(
            client,
            f"{base_url}{_FUNDING_PATH}",
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": _FUNDING_PAGE_SIZE,
            },
        )
        if not raw:
            break
        for item in raw:
            out[int(item["fundingTime"])] = float(item["fundingRate"])
        cursor = int(raw[-1]["fundingTime"]) + 1
        if len(raw) < _FUNDING_PAGE_SIZE:
            break
    return sorted(out.items())


def premium_by_open(raw: Sequence[Sequence]) -> dict[int, float]:
    """Linhas de premiumIndexKlines → {open_time_ms: close}."""
    return {int(row[0]): float(row[4]) for row in raw}


def funding_asof(
    close_times_ms: Sequence[int],
    funding: Sequence[tuple[int, float]],
) -> tuple[list[float | None], list[float | None]]:
    """Alinha funding aos candles sem lookahead.

    Para cada candle, usa a última liquidação com `fundingTime <= close_time`
    (já conhecida quando o candle fecha). Retorna (rate, change), onde
    `change` = rate da última liquidação − rate da penúltima. `None` quando
    não há liquidações suficientes antes do candle.
    """
    times = [t for t, _ in funding]
    rates = [r for _, r in funding]
    out_rate: list[float | None] = []
    out_change: list[float | None] = []
    for close_ms in close_times_ms:
        k = bisect.bisect_right(times, close_ms) - 1
        out_rate.append(rates[k] if k >= 0 else None)
        out_change.append(rates[k] - rates[k - 1] if k >= 1 else None)
    return out_rate, out_change


def funding_pipeline(funding: Sequence[tuple[int, float]]) -> FeaturePipeline:
    def _rate(c: list[Candle]) -> list[float | None]:
        return funding_asof([_to_ms(x.close_time) for x in c], funding)[0]

    def _change(c: list[Candle]) -> list[float | None]:
        return funding_asof([_to_ms(x.close_time) for x in c], funding)[1]

    return FeaturePipeline([("funding_rate", _rate), ("funding_change", _change)])


def premium_pipeline(premium: dict[int, float]) -> FeaturePipeline:
    def _premium(c: list[Candle]) -> list[float | None]:
        return [premium.get(_to_ms(x.open_time)) for x in c]

    return FeaturePipeline([("premium_close", _premium)])


class ConcatPipeline:
    """Concatena colunas de dois pipelines, mantendo só linhas válidas em ambos."""

    def __init__(self, base: FeaturePipeline, extra: FeaturePipeline) -> None:
        self._base = base
        self._extra = extra

    @property
    def names(self) -> list[str]:
        return self._base.names + self._extra.names

    def transform(
        self,
        candles: list[Candle],
        ref_candles: dict[str, list[Candle]] | None = None,
    ) -> FeatureMatrix:
        del ref_candles
        base_fm = self._base.transform(candles)
        extra_fm = self._extra.transform(candles)
        extra_pos = {idx: k for k, idx in enumerate(extra_fm.indices)}

        rows: list[np.ndarray] = []
        indices: list[int] = []
        times: list[datetime] = []
        for k, idx in enumerate(base_fm.indices):
            j = extra_pos.get(idx)
            if j is None:
                continue
            rows.append(np.concatenate([base_fm.values[k], extra_fm.values[j]]))
            indices.append(idx)
            times.append(base_fm.times[k])

        values = (
            np.vstack(rows).astype(np.float64)
            if rows
            else np.zeros((0, len(self.names)), dtype=np.float64)
        )
        return FeatureMatrix(values=values, names=self.names, indices=indices, times=times)


def _pipeline_for(
    kind: str, extras: MarketExtras | None = None
) -> FeaturePipeline | ConcatPipeline:
    extras = extras or MarketExtras()
    if kind == "taker_buy":
        return default_pipeline(include_taker_buy=True)
    if kind == "funding":
        return ConcatPipeline(default_pipeline(), funding_pipeline(extras.funding))
    if kind == "premium":
        return ConcatPipeline(
            default_pipeline(), premium_pipeline(extras.premium_by_open_ms)
        )
    return default_pipeline()


def _train_one(
    candles: list[Candle],
    *,
    symbol: str,
    interval: str,
    horizon: int,
    pipeline_kind: str,
    extras: MarketExtras | None = None,
) -> Row | None:
    pipeline = _pipeline_for(pipeline_kind, extras)
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

    variants = [k for k in kinds if k != "ohlcv"] if "ohlcv" in kinds else []
    for variant in variants:
        print(f"\n=== delta {variant} - ohlcv (wf_mean) ===")
        header = f"{'interval':<8} {'symbol':<9}" + "".join(
            f"  {lbl:>8}" for lbl in labels
        )
        print(header)
        print("-" * len(header))
        deltas: list[float] = []
        keys = sorted({(r.interval, r.symbol) for r in rows})
        for interval, symbol in keys:
            line = f"{interval:<8} {symbol:<9}"
            for h in horizons:
                base = by_key.get(("ohlcv", interval, symbol), {}).get(h)
                other = by_key.get((variant, interval, symbol), {}).get(h)
                if (
                    base is None
                    or other is None
                    or base.wf_mean is None
                    or other.wf_mean is None
                ):
                    line += f"  {'n/a':>8}"
                else:
                    delta = other.wf_mean - base.wf_mean
                    deltas.append(delta)
                    line += f"  {delta:>+8.4f}"
            print(line)
        if deltas:
            n_pos = sum(1 for d in deltas if d > 0)
            print(
                f"resumo: {n_pos}/{len(deltas)} positivos, "
                f"média {sum(deltas) / len(deltas):+.4f}, "
                f"min {min(deltas):+.4f}, max {max(deltas):+.4f}"
            )

    print("\n=== n_train por intervalo/horizonte (min-max entre símbolos) ===")
    for interval in intervals:
        parts = []
        for h in horizons:
            ns = [r.n_train for r in rows if r.interval == interval and r.horizon == h]
            if ns:
                parts.append(f"h={h}: {min(ns)}-{max(ns)}")
        if parts:
            print(f"{interval:<8} " + "  ".join(parts))

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
    if features == "all":
        # taker_buy fica de fora: hipótese rejeitada no ML-3d-2.
        return ["ohlcv", "funding", "premium"]
    return [features]


async def _fetch_extras(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    symbol: str,
    interval: str,
    candles: list[Candle],
    kinds: list[str],
) -> MarketExtras:
    funding: list[tuple[int, float]] = []
    premium: dict[int, float] = {}
    if "funding" in kinds:
        funding = await _fetch_funding(
            client,
            base_url=base_url,
            symbol=symbol,
            start_ms=_to_ms(candles[0].open_time) - _FUNDING_LOOKBACK_MS,
            end_ms=_to_ms(candles[-1].close_time),
        )
    if "premium" in kinds:
        premium = await _fetch_premium(
            client,
            base_url=base_url,
            symbol=symbol,
            interval=interval,
            total_limit=len(candles),
        )
    logger.info(
        "experiment.extras_ready",
        extra={
            "symbol": symbol,
            "interval": interval,
            "n_funding": len(funding),
            "n_funding_distinct_rates": len({r for _, r in funding}),
            "n_premium": len(premium),
        },
    )
    return MarketExtras(funding=funding, premium_by_open_ms=premium)


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

                try:
                    extras = await _fetch_extras(
                        client,
                        base_url=base_url,
                        symbol=symbol,
                        interval=interval,
                        candles=candles,
                        kinds=kinds,
                    )
                except Exception:
                    logger.exception(
                        "experiment.extras_failed",
                        extra={"symbol": symbol, "interval": interval},
                    )
                    continue

                for horizon in horizons:
                    for kind in kinds:
                        try:
                            row = _train_one(
                                candles,
                                symbol=symbol,
                                interval=interval,
                                horizon=horizon,
                                pipeline_kind=kind,
                                extras=extras,
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
            "funding=10 (funding_rate + funding_change), premium=9 "
            "(premium_close), both=ohlcv+taker_buy (default), "
            "all=ohlcv+funding+premium."
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
