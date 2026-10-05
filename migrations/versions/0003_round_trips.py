"""add round_trips table and trade round_trip_id + role

Revision ID: 0003
Revises: 0002
Create Date: 2025-10-05 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "round_trips",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("round_trip_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("close_reason", sa.String(32), nullable=True),
        sa.Column("entry_quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("entry_avg_price", sa.Numeric(28, 12), nullable=False),
        sa.Column(
            "entry_fee",
            sa.Numeric(28, 12),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "exit_quantity",
            sa.Numeric(28, 12),
            nullable=False,
            server_default="0",
        ),
        sa.Column("exit_avg_price", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "exit_fee",
            sa.Numeric(28, 12),
            nullable=False,
            server_default="0",
        ),
        sa.Column("gross_pnl", sa.Numeric(28, 12), nullable=True),
        sa.Column("net_pnl", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, index=True
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("round_trip_id", name="uq_round_trip_id"),
    )
    op.create_index(
        "ix_round_trip_symbol_status", "round_trips", ["symbol", "status"]
    )

    op.add_column(
        "trades", sa.Column("round_trip_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "trades",
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.create_index("ix_trade_round_trip_id", "trades", ["round_trip_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_round_trip_id", table_name="trades")
    op.drop_column("trades", "role")
    op.drop_column("trades", "round_trip_id")
    op.drop_index("ix_round_trip_symbol_status", table_name="round_trips")
    op.drop_table("round_trips")