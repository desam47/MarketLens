"""
Tests for backend.ai.chat_symbols — ticker resolution for the universal
AI Hub chat.

The known-symbol set and the live-quote validation are both patched so
these run offline and deterministically.
"""
import unittest
from unittest.mock import MagicMock, patch

from backend.ai import chat_symbols


def _quote(price=100.0, status="LIVE"):
    q = MagicMock()
    q.price = price
    q.data_status = status
    return q


class _Base(unittest.TestCase):
    def setUp(self):
        chat_symbols._VALID_CACHE.clear()
        # A small, fixed known-symbol universe.
        self.known = {"AAPL", "MSFT", "NVDA", "SPY", "QQQ", "SOXX", "XLK", "IWM", "^VIX", "IT"}
        p = patch.object(chat_symbols, "_known_symbols", return_value=self.known)
        p.start()
        self.addCleanup(p.stop)

    def _patch_quotes(self, mapping):
        """mapping: {symbol: Quote|None}. get_batch_quotes returns only present, non-None."""
        mgr = MagicMock()
        mgr.get_batch_quotes.side_effect = lambda syms: {
            s: mapping[s] for s in syms if mapping.get(s) is not None
        }
        self._mgr = mgr
        p = patch("backend.market_data.services.manager.market_data_manager", mgr)
        p.start()
        self.addCleanup(p.stop)
        return mgr


class TestExtractSymbols(_Base):
    def test_cashtag(self):
        self.assertEqual(chat_symbols.extract_symbols("what about $nvda today"), ["NVDA"])

    def test_paren_and_caret(self):
        self.assertEqual(chat_symbols.extract_symbols("Apple (AAPL) vs the ^VIX"), ["AAPL", "^VIX"])

    def test_bare_known_ticker_kept(self):
        self.assertEqual(chat_symbols.extract_symbols("how is NVDA trend"), ["NVDA"])

    def test_stopwords_dropped(self):
        for msg in ("is the CEO of AI EV making NEW ALL time highs",
                    "sold ALL my shares", "what is IT doing"):
            self.assertEqual(chat_symbols.extract_symbols(msg), [], msg)

    def test_it_only_via_known_or_cashtag(self):
        # "IT" is in _CHAT_STOPWORDS, so a bare mention is dropped even
        # though it's in the known set...
        self.assertEqual(chat_symbols.extract_symbols("what is IT doing"), [])
        # ...but a cashtag forces it through.
        self.assertEqual(chat_symbols.extract_symbols("watching $IT closely"), ["IT"])

    def test_all_caps_message_extracts_nothing(self):
        self.assertEqual(chat_symbols.extract_symbols("WHY IS THE MARKET DOWN SO MUCH"), [])

    def test_lowercase_known_ticker_resolved(self):
        self.assertEqual(
            chat_symbols.extract_symbols("what's support and resistance for aapl"), ["AAPL"])
        self.assertEqual(chat_symbols.extract_symbols("how's spy trending"), ["SPY"])

    def test_lowercase_unknown_word_not_a_ticker(self):
        # lowercase sweep is gated to the known set -> never probes quotes
        mgr = self._patch_quotes({"RIVN": _quote(15.0)})
        self.assertEqual(chat_symbols.extract_symbols("thoughts on rivn here"), [])
        mgr.get_batch_quotes.assert_not_called()

    def test_lowercase_stopword_collision_still_dropped(self):
        # "it" -> "IT" is in both the known set and _CHAT_STOPWORDS
        self.assertEqual(chat_symbols.extract_symbols("what is it doing today"), [])

    def test_lowercase_known_ticker_in_shouty_message(self):
        self.assertEqual(
            chat_symbols.extract_symbols("WHAT IS SUPPORT AND RESISTANCE FOR SPY"), ["SPY"])

    def test_unknown_ticker_needs_live_quote(self):
        self._patch_quotes({"RIVN": _quote(15.0)})
        self.assertEqual(chat_symbols.extract_symbols("thoughts on RIVN here"), ["RIVN"])

    def test_unknown_ticker_rejected_when_no_quote(self):
        self._patch_quotes({"ZZZZ": None})
        self.assertEqual(chat_symbols.extract_symbols("thoughts on ZZZZ"), [])

    def test_unknown_ticker_rejected_on_error_status(self):
        self._patch_quotes({"RIVN": _quote(15.0, status="ERROR")})
        self.assertEqual(chat_symbols.extract_symbols("thoughts on RIVN"), [])

    def test_validation_miss_not_cached(self):
        mgr = self._patch_quotes({"RIVN": None})
        chat_symbols.extract_symbols("RIVN?")
        chat_symbols.extract_symbols("RIVN?")
        self.assertEqual(mgr.get_batch_quotes.call_count, 2)  # re-queried, not cached

    def test_validation_hit_is_cached(self):
        mgr = self._patch_quotes({"RIVN": _quote(15.0)})
        chat_symbols.extract_symbols("RIVN?")
        chat_symbols.extract_symbols("RIVN?")
        self.assertEqual(mgr.get_batch_quotes.call_count, 1)  # 2nd from cache

    def test_group_phrase_semis_maps_to_proxy(self):
        self.assertEqual(chat_symbols.extract_symbols("how do semis look"), ["SOXX"])

    def test_group_phrase_watchlist_not_exploded(self):
        self.assertEqual(chat_symbols.extract_symbols("how is my watchlist doing"), [])

    def test_group_phrase_keeps_real_ticker(self):
        self.assertEqual(
            chat_symbols.extract_symbols("how does NVDA compare to the market"), ["NVDA"])


class TestResolveTurnSymbols(_Base):
    def test_base_symbol_first(self):
        syms, capped = chat_symbols.resolve_turn_symbols("and $MSFT?", [], ["AAPL"])
        self.assertEqual(syms, ["AAPL", "MSFT"])
        self.assertFalse(capped)

    def test_cap_at_three(self):
        syms, capped = chat_symbols.resolve_turn_symbols(
            "compare $AAPL $MSFT $NVDA $SPY $QQQ", [], [])
        self.assertEqual(len(syms), 3)
        self.assertTrue(capped)

    def test_pronoun_follow_up_inherits_prior_turn(self):
        transcript = [("user", "how's NVDA"), ("assistant", "NVDA looks strong")]
        syms, _ = chat_symbols.resolve_turn_symbols("what about its margins?", transcript, [])
        self.assertEqual(syms, ["NVDA"])

    def test_no_carry_forward_without_pronoun_or_metric(self):
        transcript = [("user", "how's NVDA")]
        syms, _ = chat_symbols.resolve_turn_symbols("nice weather today", transcript, [])
        self.assertEqual(syms, [])

    @patch.object(chat_symbols, "_ai_resolve_name")
    def test_ai_fallback_off(self, mock_ai):
        with patch.object(chat_symbols.settings.ai, "chat_symbol_ai_fallback", False):
            syms, _ = chat_symbols.resolve_turn_symbols("how is Nvidia doing", [], [])
        self.assertEqual(syms, [])
        mock_ai.assert_not_called()

    @patch.object(chat_symbols, "_ai_resolve_name", return_value=["NVDA"])
    def test_ai_fallback_on_resolves_name(self, mock_ai):
        with patch.object(chat_symbols.settings.ai, "chat_symbol_ai_fallback", True):
            syms, _ = chat_symbols.resolve_turn_symbols("how is Nvidia doing", [], [])
        self.assertEqual(syms, ["NVDA"])
        mock_ai.assert_called_once()


if __name__ == "__main__":
    unittest.main()
