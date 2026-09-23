"""Unit tests for the session_closed bucketing added to the three
independent freshness/staleness functions (regime, market-context, scanner).

Without ``session_closed``, an age past the stale threshold always reads
'stale'/'stuck' — including every evening, weekend, and holiday, which is
the bug traced from the Symbol page badge report ("Delayed · 1h ago" at
9:09 PM). Each function now buckets that same aged reading as 'closed'
when told the market is shut, leaving genuine mid-session staleness
(session_closed=False) unchanged.
"""

from backend.api.regime.router import _freshness as regime_freshness
from backend.regime.market_context_engine import MarketContextEngine
from backend.scanner.explanation import _freshness_status as scanner_freshness_status


def test_regime_freshness_buckets_closed_instead_of_stale():
    assert regime_freshness(7200, session_closed=True) == "closed"
    assert regime_freshness(7200, session_closed=False) == "stuck"
    assert regime_freshness(1800, session_closed=True) == "closed"
    assert regime_freshness(1800, session_closed=False) == "stale"


def test_regime_freshness_ignores_session_closed_when_actually_fresh():
    assert regime_freshness(10, session_closed=True) == "fresh"
    assert regime_freshness(120, session_closed=True) == "recent"


def test_regime_freshness_unknown_when_age_missing():
    assert regime_freshness(None, session_closed=True) == "unknown"


def test_market_context_engine_freshness_buckets_closed():
    assert MarketContextEngine._freshness(7200, session_closed=True) == "closed"
    assert MarketContextEngine._freshness(7200, session_closed=False) == "stuck"
    assert MarketContextEngine._freshness(1800, session_closed=True) == "closed"
    assert MarketContextEngine._freshness(1800, session_closed=False) == "stale"


def test_scanner_freshness_status_buckets_closed():
    assert scanner_freshness_status(3600, session_closed=True) == "closed"
    assert scanner_freshness_status(3600, session_closed=False) == "stale"
    assert scanner_freshness_status(30, session_closed=True) == "fresh"
