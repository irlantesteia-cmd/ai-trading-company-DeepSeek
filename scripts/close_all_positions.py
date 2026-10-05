"""Fecha todas as posições Futures abertas via ordens MARKET reduce-only.

Uso:
    python scripts/close_all_positions.py

Segurança:
    - Só opera em modo demo (`testnet=True` hardcoded).
    - `reduce_only=True` — nunca abre posição nova.
    - Idempotente: rodar 2× não faz nada na segunda vez.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.logging import setup_logging
from app.exchanges.binance.adapter import BinanceAdapter


async def main() -> None:
    setup_logging("INFO")
    logger = logging.getLogger("close_positions")

    exchange = BinanceAdapter(testnet=True)
    try:
        positions = await exchange.account.get_futures_positions()
        if not positions:
            print("Nenhuma posição aberta. Nada a fazer.")
            return

        print(f"Encontradas {len(positions)} posições abertas:")
        for p in positions:
            print(
                f"  {p.symbol:12s} {p.position_side.value:5s} "
                f"qty={p.quantity} entry={p.entry_price} "
                f"pnl={p.unrealized_pnl}"
            )
        print()

        for p in positions:
            print(f"Fechando {p.symbol} ({p.position_side.value})...")
            try:
                await exchange.positions.close_position(p.symbol, p.position_side)
                print(f"  ✅ {p.symbol} ordem de fechamento enviada")
            except Exception as exc:
                logger.exception(
                    "close.failed", extra={"symbol": p.symbol, "error": str(exc)}
                )
                print(f"  ❌ {p.symbol} falhou: {exc}")

        # Aguarda 2s para os fills propagarem e verifica novamente.
        await asyncio.sleep(2)

        remaining = await exchange.account.get_futures_positions()
        print()
        print(f"Posições abertas após fechamento: {len(remaining)}")
        for p in remaining:
            print(f"  {p.symbol} {p.position_side.value} qty={p.quantity}")
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())