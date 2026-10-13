"""shadow_trades: trades hipotéticos do modo sombra (Fase 6)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13 00:00:00

Teste para a frente sem ordens: uma linha por (hipótese, ativo, intervalo,
entrada), atualizada até a posição fechar. O role `grafana_ro` lê pelos
default privileges da 0006.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_PRICE = sa.Numeric(28, 12)
_PCT = sa.Numeric(18, 8)


def upgrade() -> None:
    op.create_table(
        "shadow_trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("hypothesis", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", _PRICE, nullable=False),
        sa.Column("quantity", _PRICE, nullable=False),
        sa.Column("stop_price", _PRICE, nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", _PRICE, nullable=True),
        sa.Column("exit_reason", sa.String(16), nullable=True),
        sa.Column("mark_price", _PRICE, nullable=True),
        sa.Column("pnl", _PRICE, nullable=False),
        sa.Column("fees", _PRICE, nullable=False),
        sa.Column("funding", _PRICE, nullable=False),
        sa.Column("return_pct", _PCT, nullable=False),
        sa.Column("gross_return_pct", _PCT, nullable=False),
        sa.Column("funding_pct", _PCT, nullable=False),
        sa.Column("bars_held", sa.Integer, nullable=False),
        sa.Column("regime_trend", sa.String(16), nullable=True),
        sa.Column("regime_vol", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("hypothesis", "symbol", "interval", "entry_time", name="uq_shadow_trades_key"),
    )
    op.create_index("ix_shadow_trades_hypothesis", "shadow_trades", ["hypothesis"])
    op.create_index("ix_shadow_trades_entry_time", "shadow_trades", ["entry_time"])


def downgrade() -> None:
    op.drop_index("ix_shadow_trades_entry_time", table_name="shadow_trades")
    op.drop_index("ix_shadow_trades_hypothesis", table_name="shadow_trades")
    op.drop_table("shadow_trades")
