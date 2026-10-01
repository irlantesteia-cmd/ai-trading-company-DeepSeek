from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Em Windows + Docker Desktop, `localhost` pode resolver para IPv6 (::1) e travar
# a conexão com o container. Forçamos IPv4 explícito quando o host é local.
_url = settings.database_url
if "@localhost:" in _url:
    _url = _url.replace("@localhost:", "@127.0.0.1:")

# Postgres em Docker (dev) não tem SSL; asyncpg 0.30+ trava o handshake se
# tentar negociar SSL contra um servidor que não suporta. Forçamos ssl=False.
_connect_args: dict = {}
if "ssl=" not in _url:
    _connect_args["ssl"] = False

engine = create_async_engine(
    _url,
    echo=settings.debug,
    connect_args=_connect_args,
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


async def check_connection(timeout: float = 5.0) -> None:
    """Executa `SELECT 1` com timeout. Levanta se o DB estiver inacessível.

    Usado no boot (falha rápida) e pela task `db_ping` do runtime (detecção
    de queda pós-boot).
    """

    async def _run() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    await asyncio.wait_for(_run(), timeout=timeout)