"""Tests for the API-only local startup mode."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from backend.market_data.services import manager_class


def test_api_startup_mode_does_not_construct_market_data_providers(monkeypatch) -> None:
    provider_class = Mock()
    settings = SimpleNamespace(
        startup_mode="api",
        market_data=SimpleNamespace(primary_provider="test", fallback_providers=[]),
    )
    monkeypatch.setattr(manager_class, "get_settings", lambda: settings)
    monkeypatch.setitem(manager_class._PROVIDER_CLASSES, "test", provider_class)

    manager = manager_class.MarketDataManager()

    assert manager.providers == {}
    provider_class.assert_not_called()
