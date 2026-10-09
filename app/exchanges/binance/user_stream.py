"""User Data Stream da Binance USDⓈ-M Futures (listenKey + WebSocket).

Responsabilidades:
- Criar/renovar/fechar `listenKey` via REST (`/fapi/v1/listenKey`).
- Conectar no WebSocket privado e emitir eventos brutos como dicts.
- Reconectar com backoff exponencial em caso de queda.
- Renovar o `listenKey` a cada `keepalive_interval_s` (metade da validade
  de 60 min) para evitar expiração.

Consumidor: `run_bot.user_stream_task` traduz os eventos brutos em
eventos de domínio (`OrderFilled`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import settings
from app.core.enums import MarketType
from app.core.exceptions import ExchangeAuthError
from app.exchanges.binance.client import BinanceClient

logger = logging.getLogger(__name__)


class UserDataStreamClient:
    """Gerencia listenKey + WebSocket privado do USDⓈ-M Futures."""

    def __init__(
        self,
        client: BinanceClient,
        *,
        keepalive_interval_s: float | None = None,
        reconnect_initial_s: float | None = None,
        reconnect_max_s: float | None = None,
        ping_interval_s: float | None = None,
    ) -> None:
        self._client = client
        self._keepalive_interval_s = (
            keepalive_interval_s
            if keepalive_interval_s is not None
            else settings.user_stream_keepalive_interval_s
        )
        self._reconnect_initial_s = (
            reconnect_initial_s
            if reconnect_initial_s is not None
            else settings.user_stream_reconnect_initial_s
        )
        self._reconnect_max_s = (
            reconnect_max_s
            if reconnect_max_s is not None
            else settings.user_stream_reconnect_max_s
        )
        self._ping_interval_s = (
            ping_interval_s
            if ping_interval_s is not None
            else settings.user_stream_ping_interval_s
        )
        self._listen_key: str | None = None
        self._stop = asyncio.Event()
        self._keepalive_task: asyncio.Task[None] | None = None

    # ---------------------------------------------------------------- REST
    async def _create_listen_key(self) -> str:
        raw = await self._client.request(
            "POST",
            "/fapi/v1/listenKey",
            market_type=MarketType.FUTURES,
            api_key_only=True,
        )
        key = raw.get("listenKey") if isinstance(raw, dict) else None
        if not key:
            raise RuntimeError(f"listenKey vazio no payload: {raw!r}")
        logger.info("user_stream.listen_key_created")
        return str(key)

    async def _keepalive_once(self) -> None:
        await self._client.request(
            "PUT",
            "/fapi/v1/listenKey",
            market_type=MarketType.FUTURES,
            api_key_only=True,
        )
        logger.debug("user_stream.keepalive_sent")

    async def _close_listen_key(self) -> None:
        try:
            await self._client.request(
                "DELETE",
                "/fapi/v1/listenKey",
                market_type=MarketType.FUTURES,
                api_key_only=True,
            )
            logger.info("user_stream.listen_key_closed")
        except Exception:
            logger.exception("user_stream.listen_key_close_failed")

    # ------------------------------------------------------------ keepalive
    async def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._keepalive_interval_s,
                )
                return
            except TimeoutError:
                pass
            try:
                await self._keepalive_once()
            except Exception:
                logger.exception("user_stream.keepalive_failed")

    # --------------------------------------------------------------- WS URL
    def _ws_url(self, listen_key: str) -> str:
        """Deriva o WS privado do REST base (demo-fapi → demo-fstream)."""
        rest_base = self._client.base_urls[MarketType.FUTURES]
        host = rest_base.split("://", 1)[-1].rstrip("/")
        ws_host = host.replace("fapi", "fstream")
        return f"wss://{ws_host}/ws/{listen_key}"

    # --------------------------------------------------------------- public
    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator de eventos brutos. Reconecta sozinho.

        Erro de credencial (`ExchangeAuthError`) aborta o iterador — não
        adianta reconectar com uma chave inválida.
        """
        backoff = self._reconnect_initial_s
        while not self._stop.is_set():
            try:
                self._listen_key = await self._create_listen_key()
                url = self._ws_url(self._listen_key)
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())

                async with websockets.connect(
                    url,
                    ping_interval=self._ping_interval_s,
                    ping_timeout=self._ping_interval_s,
                ) as ws:
                    logger.info("user_stream.ws_connected")
                    backoff = self._reconnect_initial_s

                    async for raw_message in ws:
                        if self._stop.is_set():
                            break
                        try:
                            payload = json.loads(raw_message)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(
                                "user_stream.bad_json",
                                extra={"raw": str(raw_message)[:200]},
                            )
                            continue
                        yield payload

            except asyncio.CancelledError:
                raise
            except ExchangeAuthError:
                logger.exception(
                    "user_stream.auth_failed",
                    extra={"hint": "verifique BINANCE_API_KEY/BINANCE_API_SECRET"},
                )
                await self._cleanup_roundtrip()
                return
            except ConnectionClosed as exc:
                logger.warning(
                    "user_stream.ws_closed",
                    extra={"code": exc.code, "reason": exc.reason},
                )
            except Exception:
                logger.exception("user_stream.loop_error")
            finally:
                await self._cleanup_roundtrip()

            if self._stop.is_set():
                break

            logger.info("user_stream.reconnecting", extra={"backoff_s": backoff})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                break
            except TimeoutError:
                pass
            backoff = min(backoff * 2, self._reconnect_max_s)

    async def _cleanup_roundtrip(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._keepalive_task
            self._keepalive_task = None
        if self._listen_key:
            await self._close_listen_key()
            self._listen_key = None

    async def stop(self) -> None:
        """Sinaliza parada; o loop de `stream()` sai na próxima iteração."""
        self._stop.set()