"""One row per question asked of the agent, for cost, latency and evaluation.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ask_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("answer", sa.Text),
        sa.Column("steps", sa.Integer, nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("tool_calls", JSONB, nullable=False),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_ask_log_created_at", "ask_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ask_log_created_at", table_name="ask_log")
    op.drop_table("ask_log")
