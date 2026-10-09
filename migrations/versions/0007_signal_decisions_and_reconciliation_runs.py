"""signal decisions + reconciliation_runs history

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09 00:00:00

- `signals`: decisão do TradingManager (APPROVED/REJECTED/IGNORED), motivo,
  quantidade aprovada e `client_order_id` da ordem gerada (junção com
  `orders`).
- `reconciliation_runs`: uma linha por execução do `Reconciler`.

O role `grafana_ro` lê a tabela nova pelos default privileges da 0006.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("decision", sa.String(16), nullable=True))
    op.add_column("signals", sa.Column("decision_reason", sa.Text, nullable=True))
    op.add_column(
        "signals", sa.Column("approved_quantity", sa.Numeric(28, 12), nullable=True)
    )
    op.add_column("signals", sa.Column("client_order_id", sa.String(64), nullable=True))
    op.create_index("ix_signals_decision", "signals", ["decision"])
    op.create_index("ix_signals_client_order_id", "signals", ["client_order_id"])

    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("db_open", sa.Integer, nullable=False),
        sa.Column("exchange_open", sa.Integer, nullable=False),
        sa.Column("missing_on_exchange", postgresql.JSONB, nullable=False),
        sa.Column("missing_in_db", postgresql.JSONB, nullable=False),
        sa.Column("synced", postgresql.JSONB, nullable=False),
        sa.Column("unresolved", postgresql.JSONB, nullable=False),
        sa.Column("balanced", sa.Boolean, nullable=False, index=True),
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
    )


def downgrade() -> None:
    op.drop_table("reconciliation_runs")
    op.drop_index("ix_signals_client_order_id", table_name="signals")
    op.drop_index("ix_signals_decision", table_name="signals")
    op.drop_column("signals", "client_order_id")
    op.drop_column("signals", "approved_quantity")
    op.drop_column("signals", "decision_reason")
    op.drop_column("signals", "decision")
