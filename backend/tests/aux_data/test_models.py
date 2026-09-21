"""
Phase 18 — Pydantic model validation tests for the aux_data models.
"""

import unittest
from datetime import datetime

from backend.models.aux_data import (
    FundamentalsItem,
    NewsItem,
    OptionContract,
    OptionsChain,
    OptionsType,
    UnusualActivity,
)


class TestNewsItem(unittest.TestCase):
    def test_basic_construction(self):
        item = NewsItem(
            headline="AAPL beats Q3 earnings",
            source="Reuters",
            timestamp=datetime.utcnow(),
            symbol="AAPL",
            relevance=0.85,
            url="https://example.com/apple",
        )
        self.assertEqual(item.symbol, "AAPL")
        self.assertEqual(item.relevance, 0.85)
        self.assertEqual(item.url, "https://example.com/apple")

    def test_relevance_clamped(self):
        with self.assertRaises(ValueError):
            NewsItem(
                headline="x",
                source="x",
                timestamp=datetime.utcnow(),
                symbol="AAPL",
                relevance=1.5,
            )
        with self.assertRaises(ValueError):
            NewsItem(
                headline="x",
                source="x",
                timestamp=datetime.utcnow(),
                symbol="AAPL",
                relevance=-0.1,
            )


class TestFundamentalsItem(unittest.TestCase):
    def test_minimal_required(self):
        f = FundamentalsItem(symbol="AAPL")
        self.assertEqual(f.symbol, "AAPL")
        self.assertIsNone(f.market_cap)
        self.assertIsNone(f.eps)

    def test_all_fields_set(self):
        f = FundamentalsItem(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            market_cap=3_000_000_000_000.0,
            revenue=400_000_000_000.0,
            eps=6.5,
            pe_ratio=28.0,
            institutional_ownership=0.62,
        )
        self.assertEqual(f.company_name, "Apple Inc.")
        self.assertEqual(f.pe_ratio, 28.0)
        self.assertEqual(f.institutional_ownership, 0.62)

    def test_negative_market_cap_rejected(self):
        with self.assertRaises(ValueError):
            FundamentalsItem(symbol="X", market_cap=-1.0)

    def test_institutional_ownership_clamped(self):
        with self.assertRaises(ValueError):
            FundamentalsItem(symbol="X", institutional_ownership=1.5)

    def test_negative_net_income_allowed(self):
        """Regression for a live bug (2026-09-10): net income must
        allow negative values — a company operating at a loss is real,
        meaningful data, not invalid input. (Unlike market_cap/revenue,
        which genuinely can't go negative.)"""
        f = FundamentalsItem(symbol="X", net_income=-173_471_008.0)
        self.assertEqual(f.net_income, -173_471_008.0)


class TestOptionContract(unittest.TestCase):
    def test_construction(self):
        c = OptionContract(
            strike=200.0,
            expiration="2026-12-18",
            option_type=OptionsType.CALL,
            bid=5.0,
            ask=5.5,
            volume=1000,
            open_interest=5000,
            implied_volatility=0.30,
            delta=0.45,
            in_the_money=True,
        )
        self.assertEqual(c.strike, 200.0)
        self.assertEqual(c.option_type, OptionsType.CALL)
        self.assertTrue(c.in_the_money)

    def test_delta_range(self):
        with self.assertRaises(ValueError):
            OptionContract(
                strike=200.0,
                expiration="2026-12-18",
                option_type=OptionsType.CALL,
                delta=2.0,
            )


class TestOptionsChain(unittest.TestCase):
    def test_construction(self):
        c1 = OptionContract(strike=200, expiration="2026-12-18", option_type=OptionsType.CALL)
        p1 = OptionContract(strike=200, expiration="2026-12-18", option_type=OptionsType.PUT)
        chain = OptionsChain(
            symbol="AAPL",
            expiration="2026-12-18",
            calls=[c1],
            puts=[p1],
            put_call_ratio=1.0,
        )
        self.assertEqual(chain.symbol, "AAPL")
        self.assertEqual(len(chain.calls), 1)
        self.assertEqual(chain.unusual_activity, UnusualActivity.NORMAL)


if __name__ == "__main__":
    unittest.main()
