# MarketPulse

Live crypto market data, a tool-calling AI agent and search over exchange API docs, in Python.

> Work in progress. Built in small tested parts; see the plan below.

## What it will do

- Record every BTC, ETH and SOL trade from Binance's public WebSocket feed and build one-minute candles.
- Serve prices, candles and stats over a REST API.
- Answer plain-English questions with an AI agent (free tier of Groq, or any OpenAI-compatible API) that calls the service's own tools and cites the docs it used.
- Search Binance's public Spot API docs with hybrid (vector + keyword) retrieval on PostgreSQL and pgvector.
- Send a daily market summary and price-move alerts.

It reads public market data only: no trading and no exchange account.

## Stack

Python 3.12 (uv), FastAPI, Pydantic, SQLAlchemy 2 (async) with asyncpg, Alembic, PostgreSQL 17 with pgvector,
fastembed, an OpenAI-compatible chat API (Groq free tier by default), pytest with Testcontainers, ruff, mypy
(strict), GitHub Actions.

## Run it locally

Needs Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d --wait
uv run alembic upgrade head
uv run marketpulse-docs       # download, chunk and embed the Binance Spot API docs (about 2 minutes)
uv run marketpulse            # http://localhost:8000/health and /docs
```

Run `marketpulse-docs` again at any time: documents whose ETag has not changed are skipped, and only
sections that changed are embedded again. `--force` downloads everything regardless.

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

## How docs search works

- **Source.** Six Markdown files from Binance's public
  [binance-spot-api-docs](https://github.com/binance/binance-spot-api-docs) repository (REST API, WebSocket
  streams, user data stream, errors, filters, enums), fetched with `If-None-Match` and a one-second pause
  between files.
- **Chunking.** Split at every heading, outside code blocks, so each endpoint, error code or filter is its own
  chunk with its heading path (`Public API Endpoints > Market Data endpoints > Kline/Candlestick data`) and a
  GitHub anchor for citations. Long sections are packed into chunks of about 400 tokens with a short overlap;
  code blocks are never split. Tables of contents are dropped.
- **Embeddings.** `BAAI/bge-small-en-v1.5` (384 dimensions) runs locally through fastembed: no API key, about
  580 MB of memory while ingesting. Chunks are stored by content hash, so re-ingesting adds nothing new.
- **Search.** Two rankings are merged with reciprocal rank fusion (k = 60):
  - vector: cosine distance on an HNSW index;
  - keyword: PostgreSQL full-text search where headings weigh more than body text, paths such as
    `/api/v3/klines` are split into words, and only chunks with every word of the question are kept (any word,
    when no chunk has them all).

  Each catches what the other misses: for "what does error -1021 mean", vector search alone ranks other error
  codes first and keyword search alone finds only pages that mention errors in general, while the fused list
  puts `-1021 INVALID_TIMESTAMP` first. A5 measures this on a fixed question set.

## How the agent works

`POST /ask {"question": "..."}` runs a hand-written tool-calling loop against any OpenAI-compatible chat API.
The default is Groq's free tier with `openai/gpt-oss-120b`; set `MARKETPULSE_LLM_API_KEY` (free key from
console.groq.com) and, to switch provider or model, `MARKETPULSE_LLM_BASE_URL` and `MARKETPULSE_LLM_MODEL`.

- **Tools.** `get_price`, `get_stats` (15m, 1h, 4h, 24h), `get_candles` (up to 30) and `search_docs` (up to 3
  sections, each cut to 1,500 characters to stay inside free-tier token limits). Arguments are validated with
  Pydantic before anything runs; a bad call, an unknown tool or an untracked symbol comes back to the model as
  an error it can read and correct.
- **Guardrails.** At most 6 model calls and 60 seconds per question. An answer cut off by the token limit or
  blocked by the provider's filter is reported as such, never passed off as complete. A tool call the provider
  could not parse is retried once. Rate limits are retried after the wait the provider asks for (at most 30 s).
- **Prompt injection.** Doc text goes back to the model labelled as reference data, and the system prompt tells
  it to ignore instructions inside tool results. The tools only read data; none can trade or write.
- **Answer.** Text plus the doc sections retrieved (title and link to the exact heading), every tool call,
  steps, tokens and latency. Each question is stored in `ask_log` for evaluation in A5.

## API

| Endpoint | Returns |
|----------|---------|
| `GET /prices/{symbol}` | The latest recorded trade price |
| `GET /candles/{symbol}?limit=60&cursor=` | One-minute candles, newest first, with keyset pagination: pass `next_cursor` to get the next page. Pages never skip or repeat a candle, even while new ones arrive. |
| `GET /stats/{symbol}?window=24h` | Open, last, change, change %, high, low, volume and trade count over `15m`, `1h`, `4h` or `24h` |
| `GET /docs/search?q=&limit=5&mode=hybrid` | Doc sections for a question, best first, each with its heading path, a link to the exact heading and the text. `mode` is `hybrid`, `vector` or `keyword`. |
| `POST /ask` | An answer from the agent with its sources, tool calls, steps, tokens and latency. 503 when no API key is set or the free-tier limit is reached. |
| `GET /health` | Database state, feed connection, lag and trades saved |

Prices and volumes are exact decimals, sent as strings. Errors use RFC 9457 problem details
(`application/problem+json`). Candles exist only for minutes when the ingestor was running; the API does not
fill gaps. Interactive docs are at `/docs`.

## Tests

```bash
uv run pytest                 # unit tests plus integration tests in a pgvector container
uv run pytest -m "not model"  # skip the tests that load the embedding model (about 70 MB download)
uv run pytest -m live         # one real call to the AI provider (needs MARKETPULSE_LLM_API_KEY)
uv run ruff check . && uv run mypy src tests
```

## Build plan

| Part | Scope | Status |
|------|-------|--------|
| A0 | Project skeleton, settings, health check, migrations, CI | done |
| A1 | WebSocket trade ingestor and one-minute candles | done |
| A2 | Market data API with keyset pagination | done |
| A3 | Docs ingestion and hybrid search | done |
| A4 | AI agent with tool calling and guardrails | built; live check with a key pending |
| A5 | Retrieval and answer evaluation | |
| A6 | Scheduled summary and alerts | |
| A7 | Docker image, metrics, demo | |
