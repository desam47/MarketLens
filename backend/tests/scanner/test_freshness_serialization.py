"""Scanner response freshness metadata tests."""

from datetime import UTC, datetime

from backend.api.scanner.router import _quote_to_dict
from backend.models.market_data import DataStatus, Quote


def test_scan_quote_preserves_market_data_status() -> None:
    quote = Quote(
        symbol="SPY",
        price=500.0,
        timestamp=datetime.now(UTC),
        provider="test",
        data_status=DataStatus.HISTORICAL,
    )

    response = _quote_to_dict(quote)

    assert response is not None
    assert response.data_status == "HISTORICAL"
