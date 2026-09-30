from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class BinanceWebSocket:
    """Wrapper de WebSocket com reconexão exponencial e resposta a ping aplicacional."""

    def __init__(
        self,
        *,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
    ) -> None:
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout

    async def stream(self, url: str) -> AsyncIterator[dict]:
        backoff = self._initial_backoff
        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=self._ping_interval,
                    ping_timeout=self._ping_timeout,
                    close_timeout=5.0,
                ) as ws:
                    logger.info("binance.ws.connected", extra={"url": url})
                    backoff = self._initial_backoff
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning(
                                "binance.ws.bad_json",
                                extra={"raw": str(raw)[:200]},
                            )
                            continue

                        # Binance envia ping aplicacional em texto puro.
                        if isinstance(msg, dict) and "ping" in msg:
                            await ws.send(json.dumps({"pong": msg["ping"]}))
                            continue

                        yield msg

            except (ConnectionClosed, OSError) as exc:
                logger.warning(
                    "binance.ws.disconnected",
                    extra={"url": url, "error": str(exc), "backoff": backoff},
                )
                await asyncio.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, self._max_backoff)
            except Exception:
                logger.exception("binance.ws.unexpected")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)