"""API-mode safety checks for live market-data controls."""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

from backend.api import market_data_routes


@pytest.mark.parametrize(
    "endpoint",
    [market_data_routes.start_ingestion, market_data_routes.toggle_ingestion],
)
def test_api_mode_rejects_starting_live_ingestion(monkeypatch, endpoint) -> None:
    monkeypatch.setattr(market_data_routes._settings, "startup_mode", "api")

    with pytest.raises(HTTPException, match="STARTUP_MODE=api") as exc_info:
        import asyncio

        asyncio.run(endpoint(BackgroundTasks()))

    assert exc_info.value.status_code == 409
