"""market_metrics: métricas de derivativos por ativo (coletor L5b)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12 00:00:00

Open interest, razões long/short, volume taker, funding, preço de marcação e
livro de ofertas em blocos de 5 min. O role `grafana_ro` lê pelos default
privileges da 0006.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_METRICS = (
    "open_interest",
    "open_interest_value",
    "global_ls_ratio",
    "top_ls_position_ratio",
    "taker_buy_sell_ratio",
    "taker_buy_volume",
    "taker_sell_volume",
    "mark_price",
    "index_price",
    "funding_rate",
    "book_bid_notional",
    "book_ask_notional",
    "book_imbalance",
    "spread_bps",
)


def upgrade() -> None:
    op.create_table(
        "market_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(name, sa.Numeric(28, 12), nullable=True) for name in _METRICS],
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
        sa.UniqueConstraint("symbol", "ts", name="uq_market_metrics_symbol_ts"),
    )
    op.create_index("ix_market_metrics_ts", "market_metrics", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_market_metrics_ts", table_name="market_metrics")
    op.drop_table("market_metrics")
