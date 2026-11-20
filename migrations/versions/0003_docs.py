"""Documentation chunks with embeddings and a keyword index, for hybrid search.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Slashes become spaces so a path like /api/v3/klines is indexed as the words api, v3, klines.
DOC_TSV = (
    "setweight(to_tsvector('english', translate(heading_path, '/', ' ')), 'A') || "
    "setweight(to_tsvector('english', translate(content, '/', ' ')), 'B')"
)


def upgrade() -> None:
    op.create_table(
        "doc_sources",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("url", sa.Text, nullable=False, unique=True),
        sa.Column("page_url", sa.Text, nullable=False),
        sa.Column("etag", sa.Text),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "doc_chunks",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("doc_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("heading_path", sa.Text, nullable=False),
        sa.Column("anchor", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("tsv", TSVECTOR, sa.Computed(DOC_TSV, persisted=True)),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.UniqueConstraint("source_id", "content_hash"),
    )
    op.create_index(
        "ix_doc_chunks_embedding",
        "doc_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_doc_chunks_tsv", "doc_chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("doc_chunks")
    op.drop_table("doc_sources")
