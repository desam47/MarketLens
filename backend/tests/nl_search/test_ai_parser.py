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


class TestParseQueryWithAITranslationCache(unittest.TestCase):
    """Optimization: a successful translation is cached (keyed on the
    normalized query text) so a repeated query skips the AI
    round-trip entirely. Failures must NOT be cached — a transient
    provider hiccup shouldn't permanently wall off one query string
    from ever trying AI again within the process lifetime."""

    def setUp(self):
        # Isolate from any state other tests/live traffic left behind —
        # this module-level cache persists for the process lifetime.
        from backend.nl_search import parser as parser_module
        parser_module._translation_cache.clear()

    @patch("backend.nl_search.parser.ai_manager")
    def test_second_identical_call_skips_ai(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "strongest_momentum"})
        )
        first = parse_query_with_ai("optimize cache test query one")
        self.assertIsNotNone(first)
        self.assertEqual(mock_ai.complete.call_count, 1)

        second = parse_query_with_ai("optimize cache test query one")
        self.assertIsNotNone(second)
        self.assertEqual(second.ranking, "strongest_momentum")
        # No new call — served from cache.
        self.assertEqual(mock_ai.complete.call_count, 1)

    @patch("backend.nl_search.parser.ai_manager")
    def test_cache_key_is_case_and_whitespace_insensitive(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "strongest_momentum"})
        )
        parse_query_with_ai("optimize cache test query two")
        parse_query_with_ai("  Optimize Cache Test Query Two  ")
        self.assertEqual(mock_ai.complete.call_count, 1)

    @patch("backend.nl_search.parser.ai_manager")
    def test_cached_result_is_independent_copy(self, mock_ai):
        """Mutating one caller's returned NLFilters must not corrupt
        what a later cache hit returns."""
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "strongest_momentum", "signals": ["HIGH_VOLUME"]})
        )
        first = parse_query_with_ai("optimize cache test query three")
        self.assertIsNotNone(first)
        first.signals.append("RSI_OVERSOLD")  # mutate the caller's copy

        second = parse_query_with_ai("optimize cache test query three")
        self.assertEqual(second.signals, ["HIGH_VOLUME"])

    @patch("backend.nl_search.parser.ai_manager")
    def test_failed_parse_is_not_cached_and_retries_next_call(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = _ai_response("not json at all")
        first = parse_query_with_ai("optimize cache test query four")
        self.assertIsNone(first)
        self.assertEqual(mock_ai.complete.call_count, 1)

        # Provider recovers — the same query text must try AI again,
        # not be stuck returning None forever.
        mock_ai.complete.return_value = _ai_response(
            _fenced({"ranking": "strongest_momentum"})
        )
        second = parse_query_with_ai("optimize cache test query four")
        self.assertIsNotNone(second)
        self.assertEqual(mock_ai.complete.call_count, 2)


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
