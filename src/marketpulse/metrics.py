"""Prometheus metrics. Each app has its own registry, so tests can start many apps."""

from collections.abc import Callable, Iterable
from typing import Protocol

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector


class IngestState(Protocol):
    connected: bool
    trades_saved: int
    malformed_messages: int

    def lag_seconds(self) -> float | None: ...


class IngestCollector(Collector):
    """Reads the ingestor state when Prometheus scrapes, not on every trade."""

    def __init__(self, ingestor: Callable[[], IngestState | None]) -> None:
        self._ingestor = ingestor

    def collect(self) -> Iterable[Metric]:
        ingestor = self._ingestor()
        if ingestor is None:
            return
        yield GaugeMetricFamily(
            "marketpulse_ingest_connected",
            "1 while the trade feed is connected",
            ingestor.connected,
        )
        lag = ingestor.lag_seconds()
        if lag is not None:
            yield GaugeMetricFamily(
                "marketpulse_ingest_lag_seconds", "Age of the newest trade received", lag
            )
        yield CounterMetricFamily(
            "marketpulse_trades_saved", "Trades written to the database", ingestor.trades_saved
        )
        yield CounterMetricFamily(
            "marketpulse_feed_malformed_messages",
            "Feed messages that could not be parsed",
            ingestor.malformed_messages,
        )


class Metrics:
    def __init__(self, ingestor: Callable[[], IngestState | None] = lambda: None) -> None:
        self.registry = CollectorRegistry()
        self.ask_seconds = Histogram(
            "marketpulse_ask_duration_seconds",
            "Time to answer POST /ask",
            ["status"],
            buckets=(0.5, 1, 2, 5, 10, 20, 40, 60, 120),
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "marketpulse_llm_tokens",
            "Tokens used by the AI provider",
            ["kind"],
            registry=self.registry,
        )
        self.notifications = Counter(
            "marketpulse_notifications",
            "Alerts and summaries by delivery result",
            ["kind", "status"],
            registry=self.registry,
        )
        self.registry.register(IngestCollector(ingestor))

    def record_ask(self, status: str, seconds: float, prompt: int, completion: int) -> None:
        self.ask_seconds.labels(status).observe(seconds)
        self.llm_tokens.labels("prompt").inc(prompt)
        self.llm_tokens.labels("completion").inc(completion)
