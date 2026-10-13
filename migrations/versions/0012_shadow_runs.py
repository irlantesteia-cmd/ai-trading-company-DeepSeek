"""shadow_runs: ciclos do serviço do modo sombra (Fase 6b)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13 00:00:00

Uma linha por ciclo (a cada vela de 4h), com o resumo e a saúde do serviço;
base do alerta de modo sombra parado. O role `grafana_ro` lê pelos default
privileges da 0006.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("hypothesis", sa.String(64), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_candle_close", sa.DateTime(timezone=True), nullable=True),
        sa.Column("streams", sa.Integer, nullable=False),
        sa.Column("streams_failed", sa.Integer, nullable=False),
        sa.Column("streams_stale", sa.Integer, nullable=False),
        sa.Column("streams_short_warmup", sa.Integer, nullable=False),
        sa.Column("closed_trades", sa.Integer, nullable=False),
        sa.Column("open_positions", sa.Integer, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_shadow_runs_hypothesis", "shadow_runs", ["hypothesis"])
    op.create_index("ix_shadow_runs_ran_at", "shadow_runs", ["ran_at"])


def downgrade() -> None:
    op.drop_index("ix_shadow_runs_ran_at", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_hypothesis", table_name="shadow_runs")
    op.drop_table("shadow_runs")
