"""Diagnóstico de credenciais Binance — NUNCA imprime o segredo.

Uso:
    python scripts/diagnose_binance_auth.py

Saída típica:
    API key     : len=64   has_ws=False  has_quote=False  prefix=28kDJodC...
    API secret  : len=64   has_ws=False  has_quote=False  prefix=GlRtvnR1...
    Testnet     : True

    Interpretação:
      - len == 64 e has_ws/has_quote == False  → credencial bem formatada
      - Caso contrário                        → limpar o .env
"""

from __future__ import annotations

from app.core.config import settings


def _safe_meta(label: str, value: str, prefix_len: int = 8) -> str:
    length = len(value)
    has_ws = any(c.isspace() for c in value)
    has_quote = ('"' in value) or ("'" in value)
    prefix = value[:prefix_len] if value else "(vazio)"
    return (
        f"{label:<12} len={length:<3} has_ws={has_ws!s:<5} "
        f"has_quote={has_quote!s:<5} prefix={prefix}..."
    )


def main() -> None:
    key = settings.binance_api_key
    secret = settings.binance_api_secret

    print(_safe_meta("API key", key))
    print(_safe_meta("API secret", secret))
    print(f"Testnet      : {settings.binance_testnet}")
    print(f"Recv window  : {settings.binance_recv_window_ms} ms")
    print()

    if not key or not secret:
        print("❌ Credenciais ausentes no .env")
        return

    ok = (
        len(key) == 64
        and len(secret) == 64
        and not any(c.isspace() for c in key)
        and not any(c.isspace() for c in secret)
        and '"' not in key
        and '"' not in secret
        and "'" not in key
        and "'" not in secret
    )
    if ok:
        print("✅ Formato OK.")
        print("   Se a Binance ainda rejeita, verifique:")
        print("   - A chave foi gerada no testnet CORRETO?")
        print("     • /fapi/v1/* (FUTURES) → https://testnet.binancefuture.com")
        print("     • /api/v3/* (SPOT)     → https://testnet.binance.vision")
        print("   - A chave ainda está ativa no painel do testnet?")
    else:
        print("❌ Formato suspeito. Limpe o .env:")
        print("   - Sem aspas ao redor do valor")
        print("   - Sem espaços antes ou depois do =")
        print("   - Sem espaços no final da linha")


if __name__ == "__main__":
    main()