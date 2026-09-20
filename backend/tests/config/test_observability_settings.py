"""Tests for safe observability defaults."""

from unittest.mock import patch

from backend.config.settings import ObservabilitySettings


def test_tracing_is_disabled_when_not_configured():
    """A missing tracing setting must not enable a broken exporter by default."""
    with patch.dict("os.environ", {}, clear=True):
        settings = ObservabilitySettings(_env_file=None)

    assert settings.tracing_enabled is False
    assert settings.jaeger_agent_port == 4317
