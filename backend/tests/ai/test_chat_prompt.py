"""
Tests for backend.ai.prompt.build_chat_prompt — the universal-chat
multi-block prompt assembler.
"""

import unittest

from backend.ai.prompt import CHAT_SYSTEM_PROMPT, build_chat_prompt

_WARM_BLOCK = {
    "symbol": "AAPL",
    "context": {"price": 150.0, "trend_state": {"direction": "up"}},
    "availability": {"engine_warm": True},
}
_COLD_BLOCK = {
    "symbol": "RIVN",
    "context": {"price": 12.0, "momentum": {"rsi": 61}},
    "availability": {"engine_warm": False, "note": "not in your watchlist"},
}
_MARKET = {"regime_live": {"regime": "risk_on"}, "digest": {"generated_at": "2026-09-10T16:00:00"}}


class TestBuildChatPrompt(unittest.TestCase):
    def test_market_block_and_per_symbol_blocks(self):
        p = build_chat_prompt([_WARM_BLOCK, _COLD_BLOCK], [], _MARKET, [], "compare them")
        self.assertIn("<market>", p)
        self.assertIn('<context symbol="AAPL" engine_warm="true">', p)
        self.assertIn('<context symbol="RIVN" engine_warm="false">', p)
        self.assertIn("New message: compare them", p)

    def test_unavailable_symbols_line(self):
        p = build_chat_prompt([_WARM_BLOCK], ["TSLA", "ZZZZ"], _MARKET, [], "and TSLA?")
        self.assertIn("<unavailable_symbols>TSLA, ZZZZ</unavailable_symbols>", p)

    def test_no_ticker_turn_points_at_market_only(self):
        p = build_chat_prompt([], [], _MARKET, [], "how's the market")
        self.assertIn("No ticker resolved for this turn", p)
        self.assertIn("ask which ticker they mean", p)
        self.assertNotIn("<context ", p)

    def test_alert_and_transcript_blocks_render(self):
        p = build_chat_prompt(
            [_WARM_BLOCK],
            [],
            _MARKET,
            [("user", "hi"), ("assistant", "hey")],
            "more",
            alert_context={"message": "AAPL crossed 100"},
        )
        self.assertIn("<alert_trigger>", p)
        self.assertIn("AAPL crossed 100", p)
        self.assertIn("Prior conversation", p)
        self.assertIn("user: hi", p)

    def test_capped_note_rendered_when_passed(self):
        p = build_chat_prompt(
            [_WARM_BLOCK], [], _MARKET, [], "x", capped_note="(I looked at AAPL.)"
        )
        self.assertIn("(I looked at AAPL.)", p)

    def test_token_budget_drops_oldest_transcript_first(self):
        long_hist = [("user", "word " * 500), ("user", "recent question")]
        p = build_chat_prompt(
            [_WARM_BLOCK],
            [],
            _MARKET,
            long_hist,
            "now",
            token_budget=len(CHAT_SYSTEM_PROMPT) // 4 + 400,
        )
        self.assertIn("New message: now", p)  # trailing always kept
        if "Prior conversation" in p:
            self.assertIn("[earlier conversation truncated]", p)

    def test_cold_block_marks_engine_warm_false(self):
        p = build_chat_prompt([_COLD_BLOCK], [], None, [], "RIVN?")
        self.assertIn('engine_warm="false"', p)
        self.assertIn("data_availability", p)


if __name__ == "__main__":
    unittest.main()
