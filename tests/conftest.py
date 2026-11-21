import pytest

from marketpulse.config import Settings


@pytest.fixture(autouse=True)
def ignore_the_local_env_file(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests must not depend on a developer's .env (an API key there changed what /ask returns).
    Only the live tests, which call the real provider, read it."""
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("MARKETPULSE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MARKETPULSE_TELEGRAM_BOT_TOKEN", raising=False)
    # The alert loop would run in the background of every app a test starts.
    monkeypatch.setenv("MARKETPULSE_AUTOMATION_ENABLED", "false")
