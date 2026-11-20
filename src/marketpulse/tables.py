from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
)

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
