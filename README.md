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

## Tests

```bash
uv run pytest                 # unit tests plus integration tests in a pgvector container
uv run ruff check . && uv run mypy src tests
```

## Build plan

| Part | Scope | Status |
|------|-------|--------|
| A0 | Project skeleton, settings, health check, migrations, CI | done |
| A1 | WebSocket trade ingestor and one-minute candles | next |
| A2 | Market data API with keyset pagination | |
| A3 | Docs ingestion and hybrid search | |
| A4 | Claude agent with tool calling and guardrails | |
| A5 | Retrieval and answer evaluation | |
| A6 | Scheduled summary and alerts | |
| A7 | Docker image, metrics, demo | |
