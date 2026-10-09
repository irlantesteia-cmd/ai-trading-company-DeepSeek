"""add orders.is_conditional (order persistence, infra phase 3a)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # True para ordens algo (SL/TP em /fapi/v1/algoOrder): o id é o algoId e
    # o status é consultado em endpoints próprios.
    op.add_column(
        "orders",
        sa.Column(
            "is_conditional",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "is_conditional")
