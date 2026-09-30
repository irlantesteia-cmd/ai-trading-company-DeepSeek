import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

import app.database.models  # noqa: F401  (importa todos os modelos)
from app.core.config import settings
from app.database.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _normalize_url_and_args() -> tuple[str, dict]:
    """Aplica as mesmas correções que app/database/session.py usa em runtime."""
    url = settings.database_url
    if "@localhost:" in url:
        url = url.replace("@localhost:", "@127.0.0.1:")
    connect_args: dict = {}
    if "ssl=" not in url:
        connect_args["ssl"] = False
    return url, connect_args


def run_migrations_offline() -> None:
    url, _ = _normalize_url_and_args()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url, connect_args = _normalize_url_and_args()
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())