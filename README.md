# MarketPulse

Live crypto market data, a Claude tool-calling agent and search over exchange API docs, in Python.

> Work in progress. Built in small tested parts; see the plan below.

## What it will do

- Record every BTC, ETH and SOL trade from Binance's public WebSocket feed and build one-minute candles.
- Serve prices, candles and stats over a REST API.
- Answer plain-English questions with a Claude agent that calls the service's own tools and cites the docs it used.
- Search Binance's public Spot API docs with hybrid (vector + keyword) retrieval on PostgreSQL and pgvector.
- Send a daily market summary and price-move alerts.

It reads public market data only: no trading and no exchange account.

## Stack

Python 3.12 (uv), FastAPI, Pydantic, SQLAlchemy 2 (async) with asyncpg, Alembic, PostgreSQL 17 with pgvector,
pytest with Testcontainers, ruff, mypy (strict), GitHub Actions.

## Run it locally

Needs Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d --wait
uv run alembic upgrade head
uv run marketpulse            # http://localhost:8000/health and /docs
```

## How the ingestor works

- Subscribes to Binance's combined trade stream for the configured symbols (`MARKETPULSE_SYMBOLS`).
  It uses the market-data-only host `data-stream.binance.vision` first and switches to the next feed URL
  whenever a connection ends without delivering a trade.
- Buffers trades and writes them every second or every 500 trades, in one transaction that also updates the
  one-minute candles.
- A trade the feed sends again after a reconnect is stored once and counted once: trades are keyed by
  `(symbol, trade_id)`, and candles keep the first and last trade id so batches can arrive in any order.
- Reconnects use exponential backoff with jitter (0.5 s up to 60 s). If the database is down, trades stay in
  memory (up to 50,000) and are saved on a later flush.
- `/health` shows whether the feed is connected, the lag behind the newest trade and how many trades were saved.

## API

| Endpoint | Returns |
|----------|---------|
| `GET /prices/{symbol}` | The latest recorded trade price |
| `GET /candles/{symbol}?limit=60&cursor=` | One-minute candles, newest first, with keyset pagination: pass `next_cursor` to get the next page. Pages never skip or repeat a candle, even while new ones arrive. |
| `GET /stats/{symbol}?window=24h` | Open, last, change, change %, high, low, volume and trade count over `15m`, `1h`, `4h` or `24h` |
| `GET /health` | Database state, feed connection, lag and trades saved |

Prices and volumes are exact decimals, sent as strings. Errors use RFC 9457 problem details
(`application/problem+json`). Candles exist only for minutes when the ingestor was running; the API does not
fill gaps. Interactive docs are at `/docs`.

## Tests

```bash
uv run pytest                 # unit tests plus integration tests in a pgvector container
uv run ruff check . && uv run mypy src tests
```

## Build plan

| Part | Scope | Status |
|------|-------|--------|
| A0 | Project skeleton, settings, health check, migrations, CI | done |
| A1 | WebSocket trade ingestor and one-minute candles | done |
| A2 | Market data API with keyset pagination | done |
| A3 | Docs ingestion and hybrid search | next |
| A4 | Claude agent with tool calling and guardrails | |
| A5 | Retrieval and answer evaluation | |
| A6 | Scheduled summary and alerts | |
| A7 | Docker image, metrics, demo | |
