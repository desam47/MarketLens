"""Tests for WebullFundamentalsProvider — the Webull SDK client mocked.

get_financials_indicators returns
  {"currency": "US", "values": {"<metric>": [{"fiscal_year", "fiscal_period", "value"}]}}
get_fund_brief returns {"issuer": "Apple Inc"}
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.aux_data.providers.webull_fundamentals import WebullFundamentalsProvider, _latest


def _resp(body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


def _series(*vals):
    """vals: (fy, fp, value) tuples -> a Webull metric series."""
    return [{"fiscal_year": fy, "fiscal_period": fp, "value": v} for fy, fp, v in vals]


class _FakeFundamentals:
    """Stands in for the real WebullProvider that get_cached_provider("webull")
    returns. get_fundamentals() now calls _call_provider(provider, method_name,
    sym) — i.e. getattr(provider, method_name)(sym) — so this needs the real
    WebullProvider method names/signatures (one positional arg, "US_STOCK"
    baked in), not the raw SDK client's 2-arg shape.

    ``name`` is a distinct dummy (not "webull") so these tests' failures don't
    trip the real "webull" circuit breaker's shared, module-level state and
    affect other tests or live traffic.
    """

    name = "webull_fundamentals_fake"

    def __init__(self, values=None, issuer="Apple Inc", raise_ind=False, raise_brief=False):
        self._values, self._issuer = values, issuer
        self._raise_ind, self._raise_brief = raise_ind, raise_brief

    def get_financial_indicators(self, sym):
        if self._raise_ind:
            raise RuntimeError("indicators boom")
        return _resp({"currency": "US", "values": self._values or {}})

    def get_fund_brief(self, sym):
        if self._raise_brief:
            raise RuntimeError("brief boom")
        return _resp({"issuer": self._issuer} if self._issuer else {})


def _patch_client(fake):
    return patch("backend.market_data.services.manager.get_cached_provider", return_value=fake)


class TestLatestHelper(unittest.TestCase):
    def test_picks_newest_quarter(self):
        s = _series((2025, 4, "1.0"), (2026, 3, "2.02"), (2026, 2, "2.00"))
        self.assertEqual(_latest(s), 2.02)

    def test_handles_bad_entries(self):
        self.assertIsNone(_latest([]))
        self.assertIsNone(_latest([{"fiscal_year": 2026, "fiscal_period": 1, "value": "-"}]))
        self.assertIsNone(_latest("nope"))


class TestWebullFundamentalsProvider(unittest.TestCase):
    def test_maps_diluted_eps_latest_quarter(self):
        # The real get_financials_indicators key set is per-share ratios;
        # only diluted_eps_incl_extra maps to a FundamentalsItem field.
        fake = _FakeFundamentals(
            values={
                "diluted_eps_incl_extra": _series((2025, 4, "1.5"), (2026, 3, "2.02")),
                "roe": _series((2026, 3, "1.6")),  # no FundamentalsItem home
                "net_margin": _series((2026, 3, "0.24")),
            }
        )
        with _patch_client(fake):
            prov = WebullFundamentalsProvider()
            out = prov.get_fundamentals("AAPL")
        d = out.data
        self.assertEqual(out.provider, "webull_fundamentals")
        self.assertEqual(d.company_name, "Apple Inc")
        self.assertEqual(d.eps, 2.02)  # newest quarter, not 1.5
        self.assertTrue(prov._is_healthy)

    def test_both_calls_fail_raises(self):
        with _patch_client(_FakeFundamentals(raise_ind=True, raise_brief=True)):
            prov = WebullFundamentalsProvider()
            with self.assertRaises(RuntimeError):
                prov.get_fundamentals("AAPL")
            self.assertFalse(prov._is_healthy)

    def test_no_mappable_metrics_raises(self):
        # brief resolves the name but no mappable metric keys -> raise -> yfinance fallback
        with _patch_client(_FakeFundamentals(values={"roe": _series((2026, 1, "1.4"))})):
            with self.assertRaises(RuntimeError):
                WebullFundamentalsProvider().get_fundamentals("FOO")

    def test_provider_unavailable_raises(self):
        with patch("backend.market_data.services.manager.get_cached_provider", return_value=None):
            with self.assertRaises(RuntimeError):
                WebullFundamentalsProvider().get_fundamentals("AAPL")


if __name__ == "__main__":
    unittest.main()
