"""Testa a credencial contra SPOT e FUTURES testnet — descobre qual aceita.

Uso:
    python scripts/test_binance_auth.py

Saída típica:
    SPOT     /api/v3/account    ExchangeAuthError: -2015
    FUTURES  /fapi/v2/balance   200 OK

A Binance mantém dois testnets separados:
  - SPOT:    https://testnet.binance.vision
  - FUTURES: https://testnet.binancefuture.com
Uma chave gerada em um NÃO funciona no outro.
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.enums import MarketType
from app.core.exceptions import AITradingError
from app.exchanges.binance.client import BinanceClient


async def _try(client: BinanceClient, label: str, path: str, market: MarketType) -> bool:
    """Tenta um endpoint assinado. Retorna True se 200 OK."""
    try:
        result = await client.request("GET", path, market_type=market, signed=True)
        preview = str(result)[:80]
        print(f"  {label:<25} 200 OK  {preview}")
        return True
    except AITradingError as exc:
        print(f"  {label:<25} {type(exc).__name__}: {exc}")
        return False


async def main() -> None:
    if not settings.binance_api_key or not settings.binance_api_secret:
        print("❌ Credenciais ausentes no .env")
        return

    client = BinanceClient(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=True,
    )

    print(f"Testnet: {settings.binance_testnet}")
    print(f"API key prefix: {settings.binance_api_key[:8]}...\n")

    try:
        print("SPOT (testnet.binance.vision):")
        spot_ok = await _try(
            client, "/api/v3/account", "/api/v3/account", MarketType.SPOT
        )

        print("\nFUTURES (testnet.binancefuture.com):")
        futures_ok = await _try(
            client, "/fapi/v2/balance", "/fapi/v2/balance", MarketType.FUTURES
        )

        print("\n" + "=" * 60)
        if spot_ok and futures_ok:
            print("✅ Chave funciona em ambos os testnets (raro).")
        elif spot_ok and not futures_ok:
            print("→ Chave é de SPOT testnet (testnet.binance.vision).")
            print("  Gere uma chave em https://testnet.binancefuture.com")
            print("  e substitua no .env.")
        elif futures_ok and not spot_ok:
            print("→ Chave é de FUTURES testnet (testnet.binancefuture.com).")
            print("  OK para o bot. Se SPOT falha, gere uma chave separada para SPOT.")
        else:
            print("❌ Nenhum dos testnets aceitou a chave.")
            print("  Ações:")
            print("  1. Apague a chave em ambos os testnets e gere nova.")
            print("  2. Verifique se copiou o SECRET certo (não a key).")
            print("  3. Confirme que a chave não expirou.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())