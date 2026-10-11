"""trading_halts: trava de trading persistente (R2)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10 00:00:00

Uma linha por trava. Ativa enquanto `cleared_at` for NULL; nada é apagado
(histórico para auditoria). O role `grafana_ro` lê pelos default privileges
da 0006.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trading_halts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),  # GLOBAL / SYMBOL
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),  # MANUAL / AUTO
        sa.Column("rule", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by", sa.String(64), nullable=True),
        sa.Column("clear_note", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(scope = 'GLOBAL' AND symbol IS NULL) OR (scope = 'SYMBOL' AND symbol IS NOT NULL)",
            name="ck_trading_halts_scope_symbol",
        ),
    )
    # Consulta quente: travas ativas, a cada sinal.
    op.create_index(
        "ix_trading_halts_active",
        "trading_halts",
        ["scope", "symbol"],
        postgresql_where=sa.text("cleared_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_trading_halts_active", table_name="trading_halts")
    op.drop_table("trading_halts")
