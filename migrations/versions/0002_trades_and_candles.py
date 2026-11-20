"""Trades from the live feed and the one-minute candles built from them.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICE = sa.Numeric(20, 8)
QUANTITY = sa.Numeric(28, 8)


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("trade_id", sa.BigInteger, nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("qty", QUANTITY, nullable=False),
        sa.Column("is_buyer_maker", sa.Boolean, nullable=False),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trade_id"),
    )
    op.create_index("ix_trades_symbol_traded_at", "trades", ["symbol", "traded_at"])
    op.create_table(
        "candles_1m",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("volume", QUANTITY, nullable=False),
        sa.Column("trades", sa.Integer, nullable=False),
        sa.Column("first_trade_id", sa.BigInteger, nullable=False),
        sa.Column("last_trade_id", sa.BigInteger, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "open_time"),
    )


def downgrade() -> None:
    op.drop_table("candles_1m")
    op.drop_index("ix_trades_symbol_traded_at", table_name="trades")
    op.drop_table("trades")
