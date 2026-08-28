"""Tests for the AI-driven NL query parser."""
import json
import unittest
from unittest.mock import patch

from backend.ai.provider import AIResponse
from backend.nl_search.parser import parse_query, parse_query_with_ai


def _ai_response(text: str, provider: str = "ollama") -> AIResponse:
    return AIResponse(text=text, provider=provider, model="llama3.2")


def _fenced(payload: dict) -> str:
    return "```json\n" + json.dumps(payload) + "\n```"


class TestParseQueryWithAIDisabled(unittest.TestCase):

    @patch("backend.nl_search.parser.ai_manager")
    def test_returns_none_when_ai_unavailable(self, mock_ai):
        mock_ai.is_available.return_value = False
        result = parse_query_with_ai("anything")
        self.assertIsNone(result)
        mock_ai.complete.assert_not_called()


class TestParseQueryWithAIValid(unittest.TestCase):

    @patch("backend.nl_search.parser.ai_manager")
    def test_valid_fenced_json(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({
                "ranking": "strongest_bullish",
                "direction": "bullish",
                "min_confidence": 0.6,
            })
        )
        result = parse_query_with_ai("give me bullish stocks")
        self.assertIsNotNone(result)
        self.assertEqual(result.ranking, "strongest_bullish")
        self.assertEqual(result.direction, "bullish")
        self.assertEqual(result.min_confidence, 0.6)

    @patch("backend.nl_search.parser.ai_manager")
    def test_valid_plain_json(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            json.dumps({"ranking": "strongest_momentum"})
        )
        result = parse_query_with_ai("momentum plays")
        self.assertIsNotNone(result)
        self.assertEqual(result.ranking, "strongest_momentum")

    @patch("backend.nl_search.parser.ai_manager")
    def test_match_all_default(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "strongest_bullish", "match_all": True})
        )
        result = parse_query_with_ai("weather forecast today")
        self.assertIsNotNone(result)
        self.assertTrue(result.match_all)

    @patch("backend.nl_search.parser.ai_manager")
    def test_strips_conflict_extension(self, mock_ai):
        """The AI may emit _conflict for cross-TF queries; we strip it
        before validation and pass it via the parser orchestrator."""
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({
                "timeframe": "1d",
                "direction": "bullish",
                "mtf_conflict": True,
                "_conflict": {"timeframe": "5m", "direction": "bearish"},
            })
        )
        result = parse_query_with_ai("bullish daily but bearish 5m")
        self.assertIsNotNone(result)
        self.assertEqual(result.timeframe, "1d")
        self.assertEqual(result.direction, "bullish")


class TestParseQueryWithAIFailures(unittest.TestCase):

    @patch("backend.nl_search.parser.ai_manager")
    def test_invalid_json_returns_none(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response("Sorry, I can't help with that.")
        result = parse_query_with_ai("anything")
        self.assertIsNone(result)

    @patch("backend.nl_search.parser.ai_manager")
    def test_bad_enum_returns_none(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "purple", "direction": "bullish"})
        )
        result = parse_query_with_ai("anything")
        self.assertIsNone(result)

    @patch("backend.nl_search.parser.ai_manager")
    def test_out_of_range_returns_none(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"trend_min": 150})  # out of [0, 100] range
        )
        result = parse_query_with_ai("anything")
        self.assertIsNone(result)

    @patch("backend.nl_search.parser.ai_manager")
    def test_ai_returns_none_text(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(text=None, provider="disabled")
        result = parse_query_with_ai("anything")
        self.assertIsNone(result)

    @patch("backend.nl_search.parser.ai_manager")
    def test_ai_complete_raises(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.side_effect = RuntimeError("oops")
        result = parse_query_with_ai("anything")
        self.assertIsNone(result)


class TestParseQueryOrchestrator(unittest.TestCase):

    @patch("backend.nl_search.parser.ai_manager")
    def test_ai_used_when_rules_fail(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "biggest_improvement", "direction": "bullish"})
        )
        # Garbage query, but AI understands the intent.
        f, extras, used = parse_query("improve now")
        self.assertEqual(used, "ai")
        self.assertEqual(f.ranking, "biggest_improvement")

    @patch("backend.nl_search.parser.ai_manager")
    def test_default_when_ai_off_and_rules_fail(self, mock_ai):
        mock_ai.is_available.return_value = False
        f, extras, used = parse_query("asdfghjkl")
        self.assertEqual(used, "default")
        self.assertTrue(f.match_all)


if __name__ == "__main__":
    unittest.main()
