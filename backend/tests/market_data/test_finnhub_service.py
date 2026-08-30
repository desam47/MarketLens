"""
Tests for backend/market_data/services/finnhub_service.py — FinnhubService

All HTTP calls are mocked. We test the service layer's translation
of Finnhub JSON into our Pydantic models.
"""
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

from backend.market_data.services.finnhub_service import FinnhubService


def _mock_response(status_code=200, json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.text = ""
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


class TestFinnhubServiceCompanyProfile(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_company_profile(self, mock_get):
        mock_get.return_value = _mock_response(200, {
            "country": "US", "currency": "USD", "exchange": "NASDAQ",
            "finnhubIndustry": "Technology", "ipo": "1980-12-12",
            "logo": "https://logo.url/aapl.png", "marketCapitalization": 2500000.0,
            "name": "Apple Inc", "ticker": "AAPL", "weburl": "https://apple.com",
        })
        profile = self.service.get_company_profile("AAPL")
        self.assertEqual(profile.ticker, "AAPL")
        self.assertEqual(profile.name, "Apple Inc")
        self.assertEqual(profile.finnhub_industry, "Technology")
        self.assertEqual(profile.market_capitalization, 2500000.0)

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_company_profile_invalid_symbol(self, mock_get):
        mock_get.return_value = _mock_response(200, {})
        with self.assertRaises(ValueError):
            self.service.get_company_profile("INVALID")


class TestFinnhubServiceCompanyMetrics(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_company_metrics(self, mock_get):
        mock_get.return_value = _mock_response(200, {
            "metric": {
                "peBasicExtraTTM": 28.5,
                "beta": 1.2,
                "52WeekHigh": 200.0,
                "52WeekLow": 120.0,
                "dividendYieldIndicatedAnnual": 0.005,
                "epsBasicExtraTTM": 5.5,
                "netMarginQuarterly": 0.25,
                "priceTargetMean": 180.0,
                "recommendationMean": 2.0,
            }
        })
        metrics = self.service.get_company_metrics("AAPL")
        self.assertEqual(metrics.symbol, "AAPL")
        self.assertEqual(metrics.pe_basic_eps, 28.5)
        self.assertEqual(metrics.beta, 1.2)
        self.assertEqual(metrics.high_52w, 200.0)
        self.assertEqual(metrics.low_52w, 120.0)
        self.assertEqual(metrics.target_mean_price, 180.0)

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_company_metrics_empty(self, mock_get):
        mock_get.return_value = _mock_response(200, {})
        with self.assertRaises(ValueError):
            self.service.get_company_metrics("INVALID")


class TestFinnhubServiceRecommendations(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_recommendations(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"period": "2024-Q1", "buy": 15, "hold": 10, "sell": 5,
             "strongBuy": 5, "strongSell": 1}
        ])
        recs = self.service.get_analyst_recommendations("AAPL")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].period, "2024-Q1")
        self.assertEqual(recs[0].buy, 15)
        # 20 buys out of 36 total = 0.5555...
        self.assertAlmostEqual(recs[0].buy_pct, 20/36, places=4)

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_recommendations_empty(self, mock_get):
        mock_get.return_value = _mock_response(200, [])
        with self.assertRaises(ValueError):
            self.service.get_analyst_recommendations("INVALID")


class TestFinnhubServiceInsiderSentiment(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_insider_sentiment(self, mock_get):
        mock_get.return_value = _mock_response(200, {
            "data": [
                {"symbol": "AAPL", "year": 2024, "month": 1, "change": 50000, "mspr": 0.3},
                {"symbol": "AAPL", "year": 2024, "month": 2, "change": -20000, "mspr": -0.1},
            ]
        })
        result = self.service.get_insider_sentiment("AAPL", date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].change, 50000)
        self.assertEqual(result[0].year, 2024)
        self.assertEqual(result[0].month, 1)

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_insider_sentiment_empty(self, mock_get):
        mock_get.return_value = _mock_response(200, {"data": []})
        result = self.service.get_insider_sentiment("AAPL", date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(result, [])


class TestFinnhubServiceNews(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_company_news(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"id": 1, "datetime": 1700000000, "headline": "Apple releases new iPhone",
             "source": "Reuters", "url": "https://example.com/1",
             "related": "AAPL", "summary": "Apple announced...",
             "category": "technology"}
        ])
        news = self.service.get_company_news("AAPL", date(2024, 1, 1), date(2024, 1, 7))
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].headline, "Apple releases new iPhone")
        self.assertEqual(news[0].source, "Reuters")

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_market_news(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"id": 1, "datetime": 1700000000, "headline": "Markets rise",
             "source": "Bloomberg", "url": "https://example.com/1",
             "category": "general"}
        ])
        news = self.service.get_market_news("general")
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].headline, "Markets rise")


class TestFinnhubServicePeers(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_peers(self, mock_get):
        mock_get.return_value = _mock_response(200, ["MSFT", "GOOGL", "META", "AMZN"])
        peers = self.service.get_peers("AAPL")
        self.assertEqual(peers, ["MSFT", "GOOGL", "META", "AMZN"])

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_peers_empty(self, mock_get):
        mock_get.return_value = _mock_response(200, [])
        self.assertEqual(self.service.get_peers("INVALID"), [])


class TestFinnhubServiceFinancials(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_get_company_financials(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, {"data": [
                {"revenue": 100000.0, "netIncome": 25000.0, "eps": 5.0}
            ]}),
            _mock_response(200, {"data": [
                {"totalAssets": 200000.0, "totalLiabilities": 80000.0, "totalEquity": 120000.0}
            ]}),
            _mock_response(200, {"data": [
                {"operatingCashFlow": 30000.0, "capitalExpenditure": 5000.0,
                 "investingCashFlow": -10000.0, "financingCashFlow": -5000.0}
            ]}),
        ]
        fin = self.service.get_company_financials("AAPL")
        self.assertEqual(fin.symbol, "AAPL")
        self.assertEqual(fin.total_revenue, 100000.0)
        self.assertEqual(fin.net_income, 25000.0)
        self.assertEqual(fin.total_assets, 200000.0)
        # Free cash flow = OCF - capex = 30000 - 5000 = 25000
        self.assertEqual(fin.free_cash_flow, 25000.0)


class TestFinnhubServiceAuth(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.services.finnhub_service._settings") as mock_settings:
            mock_settings.finnhub.api_key = "test_key_123"
            mock_settings.finnhub.request_timeout = 10.0
            self.service = FinnhubService()

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_api_key_passed_in_params(self, mock_get):
        mock_get.return_value = _mock_response(200, {"ticker": "AAPL", "name": "Apple"})
        self.service.get_company_profile("AAPL")
        # Verify the API key was passed in params
        call_args = mock_get.call_args
        params = call_args.kwargs.get("params", {})
        self.assertEqual(params.get("token"), "test_key_123")
        self.assertEqual(params.get("symbol"), "AAPL")

    @patch("backend.market_data.services.finnhub_service.requests.get")
    def test_rate_limit_raises_runtime_error(self, mock_get):
        mock_get.return_value = _mock_response(429, {})
        with self.assertRaises(RuntimeError) as ctx:
            self.service.get_company_profile("AAPL")
        self.assertIn("rate limited", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
