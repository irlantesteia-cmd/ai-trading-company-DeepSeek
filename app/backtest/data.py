"""Histórico de klines para o laboratório de estratégias, com cache local.

Busca na API pública da Binance Futures (dados reais, sem chave) e guarda em
`data/klines/<SYMBOL>_<interval>.json.gz` (pasta ignorada pelo git). Chamadas
seguintes reutilizam o cache e buscam só o que falta (mais antigo ou mais
novo), então o mesmo backtest é rápido e reprodutível.

Só candles **fechados** entram no cache: o REST devolve a barra em andamento
como último item, com OHLCV parcial (o mesmo problema corrigido no banco).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core.enums import MarketType
from app.domain.models.market import Candle
from app.exchanges.binance.mappers import map_kline

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://fapi.binance.com"
DEFAULT_CACHE_DIR = Path("data/klines")
_PAGE = 1500  # máximo por chamada em /fapi/v1/klines
_RETRIES = 3

_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}


def interval_ms(interval: str) -> int:
    try:
        return int(interval[:-1]) * _UNIT_MS[interval[-1]]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"intervalo não suportado: {interval!r}") from exc


class KlineCache:
    def __init__(self, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self._dir = cache_dir

    def path(self, symbol: str, interval: str) -> Path:
        return self._dir / f"{symbol.upper()}_{interval}.json.gz"

    def read(self, symbol: str, interval: str) -> list[list]:
        path = self.path(symbol, interval)
        if not path.exists():
            return []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)

    # Tentativas de troca quando outro processo está com o arquivo aberto.
    REPLACE_ATTEMPTS = 5

    def write(self, symbol: str, interval: str, rows: list[list]) -> bool:
        """Grava de forma atômica (temporário + troca). Vários laboratórios em
        paralelo podem gravar o mesmo arquivo: cada um usa um temporário
        próprio, e no Windows a troca falha enquanto outro processo lê ou troca
        o arquivo, então tenta de novo e, se não der, desiste com um aviso — os
        dados já estão em memória; só o cache deixa de ser atualizado.
        Retorna se gravou."""
        path = self.path(symbol, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(rows, f)
            for attempt in range(self.REPLACE_ATTEMPTS):
                try:
                    tmp.replace(path)
                    return True
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            logger.warning("kline_cache.write_skipped", extra={"path": str(path)})
            return False
        finally:
            tmp.unlink(missing_ok=True)


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> list[Any]:
    for attempt in range(_RETRIES):
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            if attempt == _RETRIES - 1:
                raise
            await asyncio.sleep(1.0 * (attempt + 1))
    return []


def _merge(*parts: list[list]) -> list[list]:
    by_open: dict[int, list] = {}
    for part in parts:
        for row in part:
            by_open[int(row[0])] = row
    return [by_open[k] for k in sorted(by_open)]


async def _fetch_backward(
    client: httpx.AsyncClient, url: str, symbol: str, interval: str, *, end_ms: int, start_ms: int
) -> list[list]:
    """Pagina para trás a partir de `end_ms` até cobrir `start_ms`."""
    out: list[list] = []
    cursor = end_ms
    while cursor >= start_ms:
        page = await _get(
            client, url, {"symbol": symbol, "interval": interval, "endTime": cursor, "limit": _PAGE}
        )
        if not page:
            break
        out = page + out
        cursor = int(page[0][0]) - 1
        if len(page) < _PAGE:
            break
    return out


async def _fetch_forward(
    client: httpx.AsyncClient, url: str, symbol: str, interval: str, *, start_ms: int, end_ms: int
) -> list[list]:
    """Pagina para frente a partir de `start_ms` até `end_ms`."""
    out: list[list] = []
    cursor = start_ms
    while cursor <= end_ms:
        page = await _get(
            client,
            url,
            {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": _PAGE},
        )
        if not page:
            break
        out.extend(page)
        cursor = int(page[-1][0]) + 1
        if len(page) < _PAGE:
            break
    return out


async def load_candles(
    symbol: str,
    interval: str,
    *,
    days: float,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
    cache: KlineCache | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[Candle]:
    """Candles fechados dos `days` dias anteriores a `now`, do mais antigo ao
    mais novo. `now` no passado delimita um período histórico (fora da
    amostra): nada fechado depois dele é devolvido, e o cache não perde os
    candles mais novos que já tinha."""
    now = now or datetime.now(UTC)
    cache = cache or KlineCache()
    step = interval_ms(interval)
    now_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    url = f"{base_url}/fapi/v1/klines"

    rows = cache.read(symbol, interval)
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=30)
    try:
        older: list[list] = []
        newer: list[list] = []
        if not rows:
            newer = await _fetch_backward(http, url, symbol, interval, end_ms=now_ms, start_ms=start_ms)
        else:
            if int(rows[0][0]) > start_ms:
                older = await _fetch_backward(
                    http, url, symbol, interval, end_ms=int(rows[0][0]) - 1, start_ms=start_ms
                )
            # Só busca se a barra seguinte à última do cache já fechou (a barra
            # em andamento nunca entra no cache e não pode forçar nova busca).
            if int(rows[-1][0]) + 2 * step <= now_ms:
                newer = await _fetch_forward(
                    http, url, symbol, interval, start_ms=int(rows[-1][0]) + 1, end_ms=now_ms
                )
    finally:
        if own_client:
            await http.aclose()

    # Só candles buscados agora e já fechados (close_time < now) entram no
    # cache; os que já estavam lá foram validados ao gravar e ficam.
    older = [r for r in older if int(r[6]) < now_ms]
    newer = [r for r in newer if int(r[6]) < now_ms]
    merged = _merge(older, rows, newer)
    if older or newer:
        cache.write(symbol, interval, merged)
        logger.info(
            "lab.klines_cached",
            extra={"symbol": symbol, "interval": interval, "rows": len(merged),
                   "fetched": len(older) + len(newer)},
        )

    selected = [r for r in merged if int(r[0]) >= start_ms and int(r[6]) < now_ms]
    return [map_kline(r, MarketType.FUTURES, symbol, interval, now=now) for r in selected]


# ------------------------------------------------------------------ funding
_FUNDING_PAGE = 1000  # máximo por chamada em /fapi/v1/fundingRate
_FUNDING_STEP_MS = 8 * 3_600_000  # liquidação a cada 8h (pode variar por símbolo)


async def _fetch_funding_forward(
    client: httpx.AsyncClient, url: str, symbol: str, *, start_ms: int, end_ms: int
) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    while cursor <= end_ms:
        page = await _get(
            client,
            url,
            {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": _FUNDING_PAGE},
        )
        if not page:
            break
        out.extend([int(x["fundingTime"]), float(x["fundingRate"])] for x in page)
        cursor = int(page[-1]["fundingTime"]) + 1
        if len(page) < _FUNDING_PAGE:
            break
    return out


async def load_funding(
    symbol: str,
    *,
    days: float,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
    cache: KlineCache | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[tuple[int, float]]:
    """Taxas de funding liquidadas nos últimos `days` dias: (fundingTime_ms, taxa).

    Mesmo cache dos candles (`<SYMBOL>_funding.json.gz`); só busca o que falta.
    """
    now = now or datetime.now(UTC)
    cache = cache or KlineCache()
    now_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    url = f"{base_url}/fapi/v1/fundingRate"

    rows = cache.read(symbol, "funding")
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=30)
    try:
        fetched: list[list] = []
        if not rows:
            fetched = await _fetch_funding_forward(http, url, symbol, start_ms=start_ms, end_ms=now_ms)
        else:
            if int(rows[0][0]) > start_ms + _FUNDING_STEP_MS:
                fetched += await _fetch_funding_forward(
                    http, url, symbol, start_ms=start_ms, end_ms=int(rows[0][0]) - 1
                )
            # Só busca se a próxima liquidação esperada já ficou no passado.
            if int(rows[-1][0]) + _FUNDING_STEP_MS < now_ms:
                fetched += await _fetch_funding_forward(
                    http, url, symbol, start_ms=int(rows[-1][0]) + 1, end_ms=now_ms
                )
    finally:
        if own_client:
            await http.aclose()

    merged = _merge(rows, fetched)
    if fetched:
        cache.write(symbol, "funding", merged)
    return [(int(t), float(r)) for t, r in merged if start_ms <= int(t) <= now_ms]
