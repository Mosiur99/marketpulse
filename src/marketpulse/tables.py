from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from marketpulse.docs.embedder import DIMENSIONS

metadata = MetaData()

PRICE = Numeric(20, 8)
QUANTITY = Numeric(28, 8)

trades = Table(
    "trades",
    metadata,
    Column("symbol", Text, nullable=False),
    Column("trade_id", BigInteger, nullable=False),
    Column("price", PRICE, nullable=False),
    Column("qty", QUANTITY, nullable=False),
    Column("is_buyer_maker", Boolean, nullable=False),
    Column("traded_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("symbol", "trade_id"),
)

candles_1m = Table(
    "candles_1m",
    metadata,
    Column("symbol", Text, nullable=False),
    Column("open_time", DateTime(timezone=True), nullable=False),
    Column("open", PRICE, nullable=False),
    Column("high", PRICE, nullable=False),
    Column("low", PRICE, nullable=False),
    Column("close", PRICE, nullable=False),
    Column("volume", QUANTITY, nullable=False),
    Column("trades", Integer, nullable=False),
    Column("first_trade_id", BigInteger, nullable=False),
    Column("last_trade_id", BigInteger, nullable=False),
    PrimaryKeyConstraint("symbol", "open_time"),
)

# Headings weigh more than body text in keyword search (weight A over B).
# Slashes become spaces so a path like /api/v3/klines is indexed as the words api, v3, klines.
DOC_TSV = (
    "setweight(to_tsvector('english', translate(heading_path, '/', ' ')), 'A') || "
    "setweight(to_tsvector('english', translate(content, '/', ' ')), 'B')"
)

doc_sources = Table(
    "doc_sources",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("url", Text, nullable=False, unique=True),
    Column("page_url", Text, nullable=False),
    Column("etag", Text),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
)

doc_chunks = Table(
    "doc_chunks",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "source_id", BigInteger, ForeignKey("doc_sources.id", ondelete="CASCADE"), nullable=False
    ),
    Column("heading_path", Text, nullable=False),
    Column("anchor", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("tsv", TSVECTOR, Computed(DOC_TSV, persisted=True)),
    Column("embedding", Vector(DIMENSIONS), nullable=False),
    UniqueConstraint("source_id", "content_hash"),
)

ask_log = Table(
    "ask_log",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("question", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("answer", Text),
    Column("steps", Integer, nullable=False),
    Column("prompt_tokens", Integer, nullable=False, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, server_default="0"),
    Column("latency_ms", Integer),
    Column("tool_calls", JSONB, nullable=False),
    Column("error", Text),
)
