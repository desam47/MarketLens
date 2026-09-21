"""
Phase 18 — Unit tests for the aux_data provider implementations.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.models.aux_data import FundamentalsResponse, NewsResponse, OptionsResponse


class TestYFinanceNewsProvider(unittest.TestCase):
    @patch("yfinance.Ticker")
    def test_get_news_returns_items(self, mock_ticker_cls):
        from backend.aux_data.providers.yfinance_news import YFinanceNewsProvider

        mock_ticker = MagicMock()
        mock_ticker.news = [
            {
                "title": "AAPL reports record quarter",
                "publisher": "Reuters",
                "providerPublishTime": 1710000000,
                "relatedTickers": ["AAPL", "MSFT"],
                "link": "https://example.com/apple-quarter",
            },
            {
                "title": "Tech stocks rally",
                "publisher": "Bloomberg",
                "providerPublishTime": 1709900000,
                "relatedTickers": ["MSFT", "GOOG"],
            },
        ]
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceNewsProvider()
        resp = prov.get_news("AAPL", limit=5)

        self.assertIsInstance(resp, NewsResponse)
        self.assertEqual(resp.symbol, "AAPL")
        self.assertEqual(resp.provider, "yfinance_news")
        self.assertEqual(len(resp.items), 2)
        self.assertEqual(resp.items[0].headline, "AAPL reports record quarter")
        self.assertEqual(resp.items[0].source, "Reuters")
        self.assertEqual(resp.items[0].url, "https://example.com/apple-quarter")
        self.assertEqual(resp.items[1].headline, "Tech stocks rally")
        self.assertTrue(prov._is_healthy)

    @patch("yfinance.Ticker")
    def test_get_news_handles_nested_content_shape(self, mock_ticker_cls):
        """Regression for a live bug (2026-09-09): yfinance >= ~0.2.4x
        restructured ticker.news into a nested {"id", "content": {...}}
        envelope — the flat title/publisher/providerPublishTime/
        relatedTickers shape (still covered by test_get_news_returns_items
        above, via this method's fallback-to-flat-article path) no
        longer exists. Found live: AAPL/TSLA/NVDA/MSFT all returned
        zero items after AUX_NEWS_ENABLED was turned on, even though
        no exception was raised — the parser was silently reading
        None out of every field on the new shape's top-level dict."""
        from backend.aux_data.providers.yfinance_news import YFinanceNewsProvider

        mock_ticker = MagicMock()
        mock_ticker.news = [
            {
                "id": "abc123",
                "content": {
                    "title": "Apple reveals the foldable iPhone Duo",
                    "provider": {"displayName": "Yahoo Finance Video"},
                    "pubDate": "2026-09-09T18:36:45Z",
                    "canonicalUrl": {"url": "https://finance.yahoo.com/apple-duo"},
                },
            },
        ]
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceNewsProvider()
        resp = prov.get_news("AAPL", limit=5)

        self.assertEqual(len(resp.items), 1)
        item = resp.items[0]
        self.assertEqual(item.headline, "Apple reveals the foldable iPhone Duo")
        self.assertEqual(item.source, "Yahoo Finance Video")
        self.assertEqual(item.timestamp.year, 2026)
        self.assertEqual(item.timestamp.month, 9)
        self.assertEqual(item.relevance, 0.5)
        self.assertEqual(item.url, "https://finance.yahoo.com/apple-duo")

    @patch("yfinance.Ticker")
    def test_get_news_empty_on_exception(self, mock_ticker_cls):
        from backend.aux_data.providers.yfinance_news import YFinanceNewsProvider

        mock_ticker_cls.side_effect = RuntimeError("network error")

        prov = YFinanceNewsProvider()
        resp = prov.get_news("AAPL")

        self.assertIsInstance(resp, NewsResponse)
        self.assertEqual(resp.items, [])
        self.assertFalse(prov._is_healthy)


class TestYFinanceFundamentalsProvider(unittest.TestCase):
    @patch("yfinance.Ticker")
    def test_get_fundamentals_maps_fields(self, mock_ticker_cls):
        from backend.aux_data.providers.yfinance_fundamentals import YFinanceFundamentalsProvider

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "marketCap": 3_000_000_000_000.0,
            "trailingEps": 6.42,
            "trailingPE": 30.5,
            "heldPercentInstitutions": 0.6,
            "heldPercentInsiders": 0.02,
            "beta": 1.2,
            "fiftyTwoWeekHigh": 200.0,
            "fiftyTwoWeekLow": 150.0,
            "forwardPE": 25.0,
        }
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceFundamentalsProvider()
        resp = prov.get_fundamentals("AAPL")

        self.assertIsInstance(resp, FundamentalsResponse)
        self.assertEqual(resp.data.symbol, "AAPL")
        self.assertEqual(resp.data.company_name, "Apple Inc.")
        self.assertEqual(resp.data.market_cap, 3_000_000_000_000.0)
        self.assertEqual(resp.data.eps, 6.42)
        self.assertEqual(resp.data.institutional_ownership, 0.6)
        self.assertEqual(resp.data.insider_ownership, 0.02)
        self.assertTrue(prov._is_healthy)

    @patch("yfinance.Ticker")
    def test_safe_float_handles_nan(self, mock_ticker_cls):
        from backend.aux_data.providers.yfinance_fundamentals import YFinanceFundamentalsProvider

        mock_ticker = MagicMock()
        mock_ticker.info = {"trailingPE": float("nan")}
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceFundamentalsProvider()
        resp = prov.get_fundamentals("AAPL")

        self.assertIsNone(resp.data.pe_ratio)

    @patch("yfinance.Ticker")
    def test_get_fundamentals_empty_on_exception(self, mock_ticker_cls):
        from backend.aux_data.providers.yfinance_fundamentals import YFinanceFundamentalsProvider

        mock_ticker_cls.side_effect = RuntimeError("fail")

        prov = YFinanceFundamentalsProvider()
        resp = prov.get_fundamentals("AAPL")

        self.assertEqual(resp.data.symbol, "AAPL")
        self.assertFalse(prov._is_healthy)

    @patch("yfinance.Ticker")
    def test_negative_net_income_does_not_wipe_the_rest_of_the_snapshot(self, mock_ticker_cls):
        """Regression for a live bug (2026-09-10): a real loss-making
        company (DVLT, net income -$173.5M) came back with EVERY
        fundamentals field null, not just net_income. Root cause:
        FundamentalsItem.net_income wrongly required ge=0 (fixed on
        the model directly — a loss is real data, not invalid input),
        and one field failing Pydantic validation crashed construction
        of the whole 25+-field object. This asserts both: the real
        negative value survives, and every other valid field survives
        alongside it.
        """
        from backend.aux_data.providers.yfinance_fundamentals import YFinanceFundamentalsProvider

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Datavault AI Inc.",
            "sector": "Technology",
            "marketCap": 176_018_064.0,
            "totalRevenue": 46_858_000.0,
            "netIncomeToCommon": -173_471_008.0,
            "trailingEps": 0.03,
        }
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceFundamentalsProvider()
        resp = prov.get_fundamentals("DVLT")

        self.assertEqual(resp.data.net_income, -173_471_008.0)
        self.assertEqual(resp.data.company_name, "Datavault AI Inc.")
        self.assertEqual(resp.data.sector, "Technology")
        self.assertEqual(resp.data.market_cap, 176_018_064.0)
        self.assertEqual(resp.data.revenue, 46_858_000.0)
        self.assertEqual(resp.data.eps, 0.03)

    @patch("yfinance.Ticker")
    def test_an_invalid_field_degrades_alone_not_the_whole_snapshot(self, mock_ticker_cls):
        """General case of the above: any single field that still
        fails model validation (e.g. a provider quirk we haven't seen
        yet) should drop to None on its own, not take every other
        valid field down with it."""
        from backend.aux_data.providers.yfinance_fundamentals import YFinanceFundamentalsProvider

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Example Corp",
            "sector": "Industrials",
            # priceToBook can go negative for a company with negative
            # book equity — still has ge=0 on the model, so exercise
            # the resilient-degrade path via this field specifically.
            "priceToBook": -4.5,
        }
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceFundamentalsProvider()
        resp = prov.get_fundamentals("EX")

        self.assertIsNone(resp.data.price_to_book)
        self.assertEqual(resp.data.company_name, "Example Corp")
        self.assertEqual(resp.data.sector, "Industrials")


class TestYFinanceOptionsProvider(unittest.TestCase):
    @patch("yfinance.Ticker")
    def test_get_options_builds_chain(self, mock_ticker_cls):
        import pandas as pd

        from backend.aux_data.providers.yfinance_options import YFinanceOptionsProvider

        calls_df = pd.DataFrame(
            [
                {
                    "strike": 200.0,
                    "expiration": "2026-12-18",
                    "lastPrice": 5.0,
                    "volume": 1000,
                    "openInterest": 500,
                    "impliedVolatility": 0.30,
                    "inTheMoney": False,
                    "bid": 4.8,
                    "ask": 5.2,
                },
            ]
        )
        puts_df = pd.DataFrame(
            [
                {
                    "strike": 200.0,
                    "expiration": "2026-12-18",
                    "lastPrice": 4.5,
                    "volume": 800,
                    "openInterest": 400,
                    "impliedVolatility": 0.28,
                    "inTheMoney": True,
                    "bid": 4.3,
                    "ask": 4.7,
                },
            ]
        )

        mock_opt_chain = MagicMock()
        mock_opt_chain.calls = calls_df
        mock_opt_chain.puts = puts_df

        mock_ticker = MagicMock()
        mock_ticker.options = ("2026-12-18",)
        mock_ticker.option_chain.return_value = mock_opt_chain
        mock_ticker_cls.return_value = mock_ticker

        prov = YFinanceOptionsProvider()
        resp = prov.get_options("AAPL")

        self.assertIsInstance(resp, OptionsResponse)
        self.assertEqual(resp.symbol, "AAPL")
        self.assertEqual(resp.provider, "yfinance_options")
        self.assertEqual(resp.expirations, ["2026-12-18"])
        self.assertEqual(len(resp.chains), 1)
        chain = resp.chains[0]
        self.assertEqual(len(chain.calls), 1)
        self.assertEqual(len(chain.puts), 1)
        self.assertIsNotNone(chain.put_call_ratio)
        self.assertTrue(prov._is_healthy)

    @patch("yfinance.Ticker")
    def test_get_options_empty_on_exception(self, mock_ticker_cls):
        from backend.aux_data.providers.yfinance_options import YFinanceOptionsProvider

        mock_ticker_cls.side_effect = RuntimeError("network error")

        prov = YFinanceOptionsProvider()
        resp = prov.get_options("AAPL")

        self.assertEqual(resp.symbol, "AAPL")
        self.assertEqual(resp.chains, [])
        self.assertFalse(prov._is_healthy)

    @patch("yfinance.Ticker")
    def test_get_options_keeps_chain_when_volume_or_open_interest_is_missing(self, mock_ticker_cls):
        import pandas as pd

        from backend.aux_data.providers.yfinance_options import YFinanceOptionsProvider

        calls_df = pd.DataFrame(
            [
                {
                    "strike": 200.0,
                    "volume": float("nan"),
                    "openInterest": float("nan"),
                    "impliedVolatility": 0.30,
                    "inTheMoney": False,
                }
            ]
        )
        puts_df = pd.DataFrame(
            [
                {
                    "strike": 200.0,
                    "volume": 25,
                    "openInterest": 50,
                    "impliedVolatility": 0.28,
                    "inTheMoney": True,
                }
            ]
        )
        mock_ticker = MagicMock()
        mock_ticker.options = ("2026-12-18",)
        mock_ticker.option_chain.return_value = MagicMock(calls=calls_df, puts=puts_df)
        mock_ticker_cls.return_value = mock_ticker

        response = YFinanceOptionsProvider().get_options("AAPL")

        self.assertEqual(len(response.chains), 1)
        self.assertIsNone(response.chains[0].calls[0].volume)
        self.assertIsNone(response.chains[0].calls[0].open_interest)
        self.assertEqual(response.chains[0].puts[0].volume, 25)

    def test_classify_unusual(self):
        from backend.aux_data.providers.yfinance_options import YFinanceOptionsProvider

        prov = YFinanceOptionsProvider()
        # volume=1000, oi=5000 → ratio 0.2 → normal
        self.assertEqual(prov._classify_unusual(1000, 5000).value, "normal")
        # volume=1000, oi=100 → ratio 10.0 → unusual
        self.assertEqual(prov._classify_unusual(1000, 100).value, "unusual")
        # volume=100, oi=500 → ratio 0.2 → normal
        self.assertEqual(prov._classify_unusual(100, 500).value, "normal")
        # volume=2000, oi=500 → ratio 4.0 → high
        self.assertEqual(prov._classify_unusual(2000, 500).value, "high")


if __name__ == "__main__":
    unittest.main()
