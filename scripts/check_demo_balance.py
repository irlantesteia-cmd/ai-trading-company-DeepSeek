"""Verifica ping e saldo Futures no ambiente demo/produção configurado.

Uso:
    python scripts/check_demo_balance.py
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.exchanges.binance.adapter import BinanceAdapter


async def main() -> None:
    env = "demo.binance.com" if settings.binance_testnet else "produção"
    print(f"Ambiente : {env}")
    print(f"API key  : {settings.binance_api_key[:8]}...")

    exchange = BinanceAdapter(testnet=settings.binance_testnet)
    try:
        print(f"Base URLs: {exchange._client.base_urls}")

        ping_ok = await exchange.ping()
        print(f"Ping     : {ping_ok}")

        balance = await exchange.account.get_futures_balance("USDT")
        print(
            f"USDT     : free={balance.free} "
            f"locked={balance.locked} total={balance.total}"
        )
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())