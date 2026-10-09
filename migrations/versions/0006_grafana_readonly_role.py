"""create read-only role grafana_ro for the Grafana datasource

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09 00:00:00

O Grafana consulta o Postgres direto. Com um role só de SELECT, nenhum
painel ou consulta ad-hoc consegue alterar dados. A senha vem de
`GRAFANA_DB_PASSWORD` (`.env`), a mesma que o docker-compose passa ao
datasource provisionado. Idempotente: se o role já existir, só atualiza a
senha e os grants.
"""

from alembic import op

from app.core.config import settings

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ROLE = "grafana_ro"


def _literal(value: str) -> str:
    """String SQL literal (DDL de role não aceita bind parameters)."""
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    password = _literal(settings.grafana_db_password)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} LOGIN PASSWORD {password};
            ELSE
                ALTER ROLE {ROLE} LOGIN PASSWORD {password};
            END IF;
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {ROLE}', current_database());
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}")
    # Tabelas criadas por migrations futuras também ficam legíveis.
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE}")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {ROLE}")
    op.execute(f"REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {ROLE}', current_database());
        END
        $$;
        """
    )
    op.execute(f"DROP ROLE IF EXISTS {ROLE}")
