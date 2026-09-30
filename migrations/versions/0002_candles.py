"""add candles table

Revision ID: 0002
Revises: 0001
Create Date: 2025-02-01 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column(
            "open_time", sa.DateTime(timezone=True), nullable=False, index=True
        ),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(28, 12), nullable=False),
        sa.Column("high", sa.Numeric(28, 12), nullable=False),
        sa.Column("low", sa.Numeric(28, 12), nullable=False),
        sa.Column("close", sa.Numeric(28, 12), nullable=False),
        sa.Column("volume", sa.Numeric(28, 12), nullable=False),
        sa.Column("trades", sa.Integer, nullable=False),
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
        sa.UniqueConstraint(
            "symbol",
            "market_type",
            "interval",
            "open_time",
            name="uq_candle_symbol_market_interval_time",
        ),
    )
    op.create_index(
        "ix_candle_lookup",
        "candles",
        ["symbol", "market_type", "interval", "open_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_candle_lookup", table_name="candles")
    op.drop_table("candles")