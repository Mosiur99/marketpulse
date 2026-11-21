"""Price-move alert rules and the notifications sent for them and for the daily summary.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("window", sa.Text, nullable=False),
        sa.Column("threshold_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("threshold_pct > 0", name="ck_alert_rules_threshold_positive"),
        sa.CheckConstraint("cooldown_minutes >= 1", name="ck_alert_rules_cooldown_positive"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.Text, nullable=False, unique=True),
        sa.Column("rule_id", sa.BigInteger, sa.ForeignKey("alert_rules.id", ondelete="SET NULL")),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_notifications_rule_created", "notifications", ["rule_id", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_rule_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("alert_rules")
