"""add candles.taker_buy_base_volume (ML-3d-2)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candles",
        sa.Column("taker_buy_base_volume", sa.Numeric(28, 12), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candles", "taker_buy_base_volume")
