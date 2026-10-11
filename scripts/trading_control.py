"""Pausa, retoma e mostra a trava de trading (intervenção humana).

A trava bloqueia NOVAS entradas na hora (o bot consulta a cada sinal, sem
reiniciar). Não fecha posições: para isso use scripts/close_all_positions.py.

Uso (PowerShell):
    python scripts/trading_control.py status
    python scripts/trading_control.py pause --reason "manutenção"
    python scripts/trading_control.py pause --symbol XRPUSDT --reason "spread alto"
    python scripts/trading_control.py resume --note "revisado"
    python scripts/trading_control.py resume --symbol XRPUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from app.core.config import settings
from app.database.session import AsyncSessionLocal
from app.risk.engine import RiskEngine
from app.runtime.trading_control import get_status, pause, resume


def _fmt(halt) -> str:
    where = "GLOBAL" if halt.scope == "GLOBAL" else halt.symbol
    line = (
        f"  [{where}] {halt.source}/{halt.rule}: {halt.reason}\n"
        f"      criada {halt.created_at:%Y-%m-%d %H:%M UTC} por {halt.created_by}"
    )
    if halt.cleared_at is not None:
        line += f" | liberada {halt.cleared_at:%Y-%m-%d %H:%M UTC} por {halt.cleared_by}"
        if halt.clear_note:
            line += f" ({halt.clear_note})"
    return line


async def _print_risk_numbers() -> None:
    """P&L do dia e perdas seguidas, para decidir com os números na frente."""
    if not (settings.binance_api_key and settings.binance_api_secret):
        print("  (sem credenciais da Binance: números de risco indisponíveis)")
        return
    from app.agents.risk import _limits_from_settings
    from app.exchanges.binance.adapter import BinanceAdapter
    from app.portfolio.state_loader import load_portfolio_state

    exchange = BinanceAdapter(testnet=settings.binance_testnet)
    try:
        state = await load_portfolio_state(exchange, AsyncSessionLocal)
    finally:
        await exchange.close()
    engine = RiskEngine(_limits_from_settings(settings))
    print(
        f"  P&L do dia (UTC): {state.daily_pnl:.2f} USDT "
        f"(limite -{engine.daily_loss_limit(state.equity):.2f})\n"
        f"  Perdas seguidas: {state.consecutive_losses} "
        f"(limite {settings.risk_max_consecutive_losses})\n"
        f"  Posições abertas: {state.open_positions_count} | equity {state.equity:.2f}"
    )


async def _status() -> None:
    status = await get_status(AsyncSessionLocal)
    print("TRADING:", "TRAVADO" if status.active else "ATIVO")
    if status.active:
        print("Travas ativas:")
        for h in status.active:
            print(_fmt(h))
    print("Risco agora:")
    await _print_risk_numbers()
    if status.recent_cleared:
        print("Últimas liberadas:")
        for h in status.recent_cleared[:5]:
            print(_fmt(h))


async def _pause(symbol: str | None, reason: str) -> None:
    halt = await pause(AsyncSessionLocal, reason=reason, by=getpass.getuser(), symbol=symbol)
    where = symbol.upper() if symbol else "GLOBAL"
    if halt is None:
        print(f"Já havia uma pausa manual ativa em {where}. Nada mudou.")
    else:
        print(f"Pausado ({where}): novas entradas bloqueadas. Posições abertas seguem.")


async def _resume(symbol: str | None, note: str | None) -> None:
    print("Risco antes de liberar:")
    await _print_risk_numbers()
    n = await resume(AsyncSessionLocal, by=getpass.getuser(), symbol=symbol, note=note)
    where = symbol.upper() if symbol else "GLOBAL"
    if n == 0:
        print(f"Nenhuma trava ativa em {where}.")
    else:
        print(f"Liberadas {n} trava(s) em {where}. Novas entradas voltam a ser avaliadas.")
        print("Obs.: se um limite (perda diária/perdas seguidas) continuar estourado,")
        print("o próximo sinal recria a trava automaticamente.")


def main() -> None:
    p = argparse.ArgumentParser(description="Controle da trava de trading.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="mostra travas ativas e números de risco")
    sp = sub.add_parser("pause", help="bloqueia novas entradas")
    sp.add_argument("--symbol", help="só este símbolo (padrão: global)")
    sp.add_argument("--reason", required=True, help="motivo (fica registrado)")
    sr = sub.add_parser("resume", help="libera as travas do escopo")
    sr.add_argument("--symbol", help="só este símbolo (padrão: global)")
    sr.add_argument("--note", help="observação da liberação")
    args = p.parse_args()

    if args.cmd == "status":
        asyncio.run(_status())
    elif args.cmd == "pause":
        asyncio.run(_pause(args.symbol, args.reason))
    else:
        asyncio.run(_resume(args.symbol, args.note))


if __name__ == "__main__":
    main()
