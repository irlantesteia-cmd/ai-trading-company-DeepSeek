from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.enums import MarketType
from app.core.exceptions import (
    ExchangeAuthError,
    ExchangeConnectionError,
    ExchangeError,
    ExchangeOrderRejectedError,
    ExchangeRateLimitError,
)

logger = logging.getLogger(__name__)

# Produção
SPOT_REST_PROD = "https://api.binance.com"
FUTURES_REST_PROD = "https://fapi.binance.com"

# Demo Trading (substitui o antigo testnet.binancefuture.com)
SPOT_REST_DEMO = "https://demo-api.binance.com"
FUTURES_REST_DEMO = "https://demo-fapi.binance.com"

_WEIGHT_LIMIT = {
    MarketType.SPOT: 1200,
    MarketType.FUTURES: 2400,
}


class BinanceClient:
    """Cliente REST assinado (HMAC-SHA256).

    Endpoints:
        - Produção: api.binance.com / fapi.binance.com
        - Demo Trading: demo-api.binance.com / demo-fapi.binance.com
          (a Binance descontinuou o testnet.binancefuture.com em 2026)

    Recursos:
        - Time sync com /time por mercado (compensação de clock drift).
        - Retry exponencial em 5xx / rate-limit / rede.
        - Re-sync automático em -1021 / -1022 (assinatura inválida).
        - Requests assinadas NÃO prosseguem se o time sync falhar.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = True,
        timeout: float = 15.0,
        max_retries: int = 3,
        recv_window_ms: int = 5000,
        time_sync_attempts: int = 2,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._max_retries = max_retries
        self._recv_window_ms = recv_window_ms
        self._time_sync_attempts = max(1, time_sync_attempts)
        self._weight_used: dict[MarketType, int] = {
            MarketType.SPOT: 0,
            MarketType.FUTURES: 0,
        }
        self._time_offset_ms: dict[MarketType, int] = {
            MarketType.SPOT: 0,
            MarketType.FUTURES: 0,
        }
        self._time_synced: dict[MarketType, bool] = {
            MarketType.SPOT: False,
            MarketType.FUTURES: False,
        }
        self._time_sync_lock = asyncio.Lock()
        self._base_urls = {
            MarketType.SPOT: SPOT_REST_DEMO if testnet else SPOT_REST_PROD,
            MarketType.FUTURES: FUTURES_REST_DEMO if testnet else FUTURES_REST_PROD,
        }
        self._http = httpx.AsyncClient(timeout=timeout)

    @property
    def base_urls(self) -> dict[MarketType, str]:
        return dict(self._base_urls)

    @property
    def weight_used(self) -> dict[MarketType, int]:
        return dict(self._weight_used)

    @property
    def time_offset_ms(self) -> dict[MarketType, int]:
        return dict(self._time_offset_ms)

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ signing
    def _sign_query(self, query_string: str) -> str:
        return hmac.new(
            self._api_secret.encode(),
            query_string.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _build_url(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any],
        *,
        signed: bool,
        market_type: MarketType,
    ) -> str:
        query = dict(params)
        if signed:
            query["timestamp"] = (
                int(time.time() * 1000) + self._time_offset_ms[market_type]
            )
            query["recvWindow"] = self._recv_window_ms

        qs = urlencode(query)
        if signed:
            signature = self._sign_query(qs)
            qs = f"{qs}&signature={signature}"

        if not qs:
            return f"{base_url}{path}"
        return f"{base_url}{path}?{qs}"

    # -------------------------------------------------------------- time sync
    async def sync_time(self, market_type: MarketType) -> int:
        async with self._time_sync_lock:
            return await self._do_sync_time(market_type)

    async def _do_sync_time(self, market_type: MarketType) -> int:
        path = "/api/v3/time" if market_type is MarketType.SPOT else "/fapi/v1/time"
        url = f"{self._base_urls[market_type]}{path}"

        local_before = int(time.time() * 1000)
        response = await self._http.get(url)
        local_after = int(time.time() * 1000)
        response.raise_for_status()
        server_time = int(response.json()["serverTime"])
        local_mid = (local_before + local_after) // 2
        offset = server_time - local_mid

        self._time_offset_ms[market_type] = offset
        self._time_synced[market_type] = True
        logger.info(
            "binance.time_synced",
            extra={
                "market": market_type.value,
                "offset_ms": offset,
                "rtt_ms": local_after - local_before,
            },
        )
        return offset

    async def _ensure_time_synced(self, market_type: MarketType) -> None:
        if self._time_synced[market_type]:
            return

        last_exc: Exception | None = None
        for attempt in range(self._time_sync_attempts):
            try:
                await self._do_sync_time(market_type)
                return
            except (httpx.HTTPError, OSError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "binance.time_sync_failed",
                    extra={
                        "market": market_type.value,
                        "attempt": attempt,
                        "error": str(exc) or type(exc).__name__,
                        "error_type": type(exc).__name__,
                    },
                )
                if attempt + 1 < self._time_sync_attempts:
                    await asyncio.sleep(0.5 * (attempt + 1))

        raise ExchangeConnectionError(
            f"time sync falhou para {market_type.value} após "
            f"{self._time_sync_attempts} tentativas: {last_exc}"
        )

    # ------------------------------------------------------------------ request
    async def request(
        self,
        method: str,
        path: str,
        *,
        market_type: MarketType,
        signed: bool = False,
        params: dict[str, Any] | None = None,
    ) -> Any:
        base = self._base_urls[market_type]
        base_params = dict(params or {})

        if signed:
            await self._ensure_time_synced(market_type)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            url = self._build_url(
                base,
                path,
                base_params,
                signed=signed,
                market_type=market_type,
            )
            headers: dict[str, str] = {}
            if signed:
                headers["X-MBX-APIKEY"] = self._api_key

            try:
                response = await self._http.request(method, url, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "binance.http_error",
                    extra={
                        "error": str(exc) or type(exc).__name__,
                        "error_type": type(exc).__name__,
                        "attempt": attempt,
                        "path": path,
                    },
                )
                await asyncio.sleep(self._backoff(attempt))
                continue

            self._track_weight(market_type, response)

            if response.status_code == 200:
                return response.json()

            if response.status_code == 400 and self._is_signature_error(response):
                self._log_signature_diagnostic(market_type, path, response)
                logger.warning(
                    "binance.timestamp_resync",
                    extra={"attempt": attempt, "path": path},
                )
                try:
                    await self._do_sync_time(market_type)
                except (httpx.HTTPError, OSError, ValueError):
                    logger.exception("binance.timestamp_resync_failed")
                await asyncio.sleep(self._backoff(attempt) * 0.5)
                continue

            if response.status_code in (418, 429):
                await asyncio.sleep(self._backoff(attempt) + 1.0)
                last_exc = ExchangeRateLimitError(response.text)
                continue

            if 500 <= response.status_code < 600:
                await asyncio.sleep(self._backoff(attempt))
                last_exc = ExchangeError(f"{response.status_code}: {response.text}")
                continue

            self._raise_for_status(response)

        raise ExchangeConnectionError(
            f"Binance request falhou após {self._max_retries + 1} tentativas: {last_exc}"
        )

    # ------------------------------------------------------------------ helpers
    def _track_weight(self, market_type: MarketType, response: httpx.Response) -> None:
        header = response.headers.get("X-MBX-USED-WEIGHT-1M")
        if header is None:
            return
        try:
            self._weight_used[market_type] = int(header)
        except ValueError:
            return
        limit = _WEIGHT_LIMIT[market_type]
        if self._weight_used[market_type] >= limit * 0.9:
            logger.warning(
                "binance.weight_high",
                extra={
                    "market": market_type.value,
                    "used": self._weight_used[market_type],
                    "limit": limit,
                },
            )

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0**attempt, 10.0)

    @staticmethod
    def _is_signature_error(response: httpx.Response) -> bool:
        try:
            code = response.json().get("code")
        except ValueError:
            return False
        return code in (-1021, -1022)

    def _log_signature_diagnostic(
        self,
        market_type: MarketType,
        path: str,
        response: httpx.Response,
    ) -> None:
        key = self._api_key
        secret = self._api_secret
        logger.error(
            "binance.signature_rejected_diagnostic",
            extra={
                "market": market_type.value,
                "path": path,
                "body": response.text[:200],
                "api_key_len": len(key),
                "api_key_has_whitespace": any(c.isspace() for c in key),
                "api_key_has_quote": ('"' in key) or ("'" in key),
                "secret_len": len(secret),
                "secret_has_whitespace": any(c.isspace() for c in secret),
                "secret_has_quote": ('"' in secret) or ("'" in secret),
                "hint": (
                    "Se secret_len != 64 ou *_has_whitespace/*_has_quote for "
                    "True, limpe o .env. Se estiver OK, gere nova chave em "
                    "https://demo.binance.com/en/my/settings/api-management"
                ),
            },
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {"msg": response.text, "code": None}
        code = payload.get("code")
        msg = payload.get("msg", "unknown error")

        if response.status_code == 401 or code in (-2015, -2014):
            raise ExchangeAuthError(f"{code}: {msg}")
        if code in (-1013,):
            raise ExchangeRateLimitError(f"{code}: {msg}")
        if code in (-2010, -2011, -2018, -2019, -2021, -2022, -4003, -4131):
            raise ExchangeOrderRejectedError(f"{code}: {msg}")
        raise ExchangeError(f"{response.status_code} {code}: {msg}")