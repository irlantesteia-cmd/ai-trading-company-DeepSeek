"""equity_snapshots: curva de capital para o limite de drawdown (Fase 5c)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13 00:00:00

Uma linha por reconciliação (5 min): saldo da carteira, P&L não realizado e
a soma. O role `grafana_ro` lê pelos default privileges da 0006.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wallet_balance", sa.Numeric(28, 12), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(28, 12), nullable=False),
        sa.Column("total_equity", sa.Numeric(28, 12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_equity_snapshots_taken_at", "equity_snapshots", ["taken_at"])


def downgrade() -> None:
    op.drop_index("ix_equity_snapshots_taken_at", table_name="equity_snapshots")
    op.drop_table("equity_snapshots")
