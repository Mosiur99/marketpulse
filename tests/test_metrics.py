from dataclasses import dataclass

from prometheus_client import generate_latest

from marketpulse.metrics import Metrics


@dataclass
class FakeIngestor:
    connected: bool = True
    trades_saved: int = 1234
    malformed_messages: int = 2
    lag: float | None = 0.4

    def lag_seconds(self) -> float | None:
        return self.lag


def value(metrics: Metrics, name: str, **labels: str) -> float | None:
    return metrics.registry.get_sample_value(name, labels)


def test_ingest_state_is_read_at_scrape_time() -> None:
    ingestor = FakeIngestor()
    metrics = Metrics(lambda: ingestor)
    assert value(metrics, "marketpulse_ingest_connected") == 1.0
    assert value(metrics, "marketpulse_ingest_lag_seconds") == 0.4
    assert value(metrics, "marketpulse_trades_saved_total") == 1234
    assert value(metrics, "marketpulse_feed_malformed_messages_total") == 2
    ingestor.trades_saved, ingestor.connected, ingestor.lag = 1300, False, None
    assert value(metrics, "marketpulse_trades_saved_total") == 1300
    assert value(metrics, "marketpulse_ingest_connected") == 0.0
    assert value(metrics, "marketpulse_ingest_lag_seconds") is None


def test_no_ingestor_means_no_ingest_metrics() -> None:
    text = generate_latest(Metrics().registry).decode()
    assert "marketpulse_ingest" not in text
    assert "marketpulse_ask_duration_seconds" in text


def test_record_ask_counts_time_and_tokens() -> None:
    metrics = Metrics()
    metrics.record_ask("answered", 2.5, 900, 100)
    metrics.record_ask("answered", 0.7, 100, 10)
    assert value(metrics, "marketpulse_ask_duration_seconds_count", status="answered") == 2
    assert value(metrics, "marketpulse_ask_duration_seconds_sum", status="answered") == 3.2
    assert (
        value(metrics, "marketpulse_ask_duration_seconds_bucket", status="answered", le="1.0") == 1
    )
    assert value(metrics, "marketpulse_llm_tokens_total", kind="prompt") == 1000
    assert value(metrics, "marketpulse_llm_tokens_total", kind="completion") == 110


def test_two_apps_do_not_share_a_registry() -> None:
    first, second = Metrics(), Metrics()
    first.record_ask("answered", 1, 1, 1)
    assert value(second, "marketpulse_llm_tokens_total", kind="prompt") is None
