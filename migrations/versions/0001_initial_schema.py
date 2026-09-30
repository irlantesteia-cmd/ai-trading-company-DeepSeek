"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trading_pairs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("base", sa.String(16), nullable=False),
        sa.Column("quote", sa.String(16), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("price_precision", sa.Integer, nullable=False),
        sa.Column("quantity_precision", sa.Integer, nullable=False),
        sa.Column("min_notional", sa.Numeric(28, 12), nullable=False),
        sa.Column("min_quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("step_size", sa.Numeric(28, 12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="TRADING"),
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
        sa.UniqueConstraint("symbol", "market_type", name="uq_pair_symbol_market"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("exchange_order_id", sa.String(64), nullable=False, index=True),
        sa.Column("client_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column(
            "executed_quantity",
            sa.Numeric(28, 12),
            nullable=False,
            server_default="0",
        ),
        sa.Column("price", sa.Numeric(28, 12), nullable=True),
        sa.Column("average_price", sa.Numeric(28, 12), nullable=True),
        sa.Column("stop_price", sa.Numeric(28, 12), nullable=True),
        sa.Column("time_in_force", sa.String(8), nullable=True),
        sa.Column(
            "reduce_only",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("position_side", sa.String(8), nullable=True),
        sa.Column("exchange_created_at", sa.DateTime(timezone=True), nullable=False),
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

    op.create_table(
        "order_fills",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(28, 12), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("commission", sa.Numeric(28, 12), nullable=False),
        sa.Column("commission_asset", sa.String(16), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
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

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trade_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("order_id", sa.String(64), nullable=False, index=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=True),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("price", sa.Numeric(28, 12), nullable=False),
        sa.Column("fee", sa.Numeric(28, 12), nullable=False),
        sa.Column("fee_asset", sa.String(16), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
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

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("signal_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("suggested_entry", sa.Numeric(28, 12), nullable=True),
        sa.Column("suggested_stop", sa.Numeric(28, 12), nullable=True),
        sa.Column("suggested_target", sa.Numeric(28, 12), nullable=True),
        sa.Column("regime", sa.String(16), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False, index=True),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
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

    op.create_table(
        "futures_position_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 12), nullable=False),
        sa.Column("mark_price", sa.Numeric(28, 12), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(28, 12), nullable=False),
        sa.Column("leverage", sa.Integer, nullable=False),
        sa.Column("margin_type", sa.String(16), nullable=False),
        sa.Column("liquidation_price", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
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
    for table in (
        "futures_position_snapshots",
        "signals",
        "trades",
        "order_fills",
        "orders",
        "trading_pairs",
    ):
        op.drop_table(table)