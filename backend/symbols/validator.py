"""
Symbol validation against the market data layer.

A symbol is considered valid if the configured market data provider returns
a quote with a positive price. The check uses the global
``market_data_manager``, which (after Phase 2 closure) retries each
provider up to 3 times before falling through, so transient upstream
errors don't immediately mark a symbol as invalid.

The validator returns a structured result instead of raising so callers
(e.g. the watchlist import endpoint) can build per-symbol error reports
without a single bad ticker aborting the whole batch.
"""
from pydantic import BaseModel

from backend.market_data.services.manager import market_data_manager


class ValidationResult(BaseModel):
    """Outcome of validating a single ticker symbol."""

    symbol: str
    valid: bool
    error: str | None = None


def validate_symbol(symbol: str) -> ValidationResult:
    """Validate a ticker symbol by attempting a quote fetch.

    The symbol is uppercased and stripped before validation. A symbol is
    ``valid`` when the provider returns a quote with ``price > 0``. Any
    exception from the provider is captured into ``error`` and the symbol
    is reported as invalid.
    """
    cleaned = symbol.upper().strip()
    try:
        quote = market_data_manager.get_quote(cleaned)
        if quote.price > 0:
            return ValidationResult(symbol=cleaned, valid=True)
        return ValidationResult(
            symbol=cleaned,
            valid=False,
            error=f"No price data (price={quote.price})",
        )
    except Exception as e:  # noqa: BLE001 - intentional broad catch: never raise
        return ValidationResult(symbol=cleaned, valid=False, error=str(e))
