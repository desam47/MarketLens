"""
Phase 16 — Tests for AI market analysis.

Covers the four main guarantees the spec calls out:

1. AI never calculates raw indicators — the context dict is built
   entirely from existing engine state.
2. Output is structured (Pydantic-validated).
3. AI never overwrites quantitative truth — the response is purely
   for UI; the engine's score lives elsewhere.
4. Insufficient data → uncertainty response (not an AI answer).

Plus parse-prompt-validation coverage and a mocked end-to-end test
that exercises the full path: context → prompt → mock provider →
parsed response.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from datetime import UTC

from backend.ai.analyze import (
    _adaptive_temperature,
    _calibrate_confidence,
    _clear_analysis_cache,
    _data_quality,
    analyze_symbol,
)
from backend.ai.context import (
    AnalysisContext,
    InsufficientDataError,
    build_context,
)
from backend.ai.prompt import (
    SYSTEM_PROMPT,
    AnalysisResponse,
    UncertaintyResponse,
    build_user_prompt,
    make_analysis_response_format,
    parse_ai_reply,
    render_system_prompt,
    summarize_context,
)
from backend.ai.provider import AIResponse
from backend.scanner.scanner import ScanResult


def _fake_scan_result() -> ScanResult:
    """Return a ScanResult with enough populated fields for build_context tests."""
    from datetime import datetime


    result = ScanResult("AAPL", datetime.now(UTC))
    result.quote = MagicMock()
    result.quote.price = 185.0
    result.quote.timestamp = datetime.now(UTC)
    # Trend signals for 3 timeframes — enough for len(timeframe_scores) > 0.
    result.trend_signals = {
        "ONE_DAY": {
            "direction": "strong_bullish",
            "strength": "strong",
            "confidence": 0.85,
        },
        "ONE_HOUR": {
            "direction": "bullish",
            "strength": "moderate",
            "confidence": 0.70,
        },
        "FIFTEEN_MINUTE": {
            "direction": "strong_bullish",
            "strength": "strong",
            "confidence": 0.80,
        },
    }
    result.indicator_values = {
        "rsi": 62.0,
        "macd": 1.5,
        "volume": 50_000_000,
    }
    result.scores = {"total_score": 72.5}
    result.signals = ["bullish_trend", "high_volume"]
    return result


# --- parse_ai_reply -------------------------------------------------


class TestParseAIReply(unittest.TestCase):

    def test_parses_fenced_json(self):
        text = "Some preamble.\n```json\n{\"summary\": \"AAPL is up.\", \"trend\": \"bullish\", \"confidence\": 0.8}\n```\nMore text after."
        result = parse_ai_reply(text)
        self.assertEqual(result.summary, "AAPL is up.")
        self.assertEqual(result.trend, "bullish")
        self.assertAlmostEqual(result.confidence, 0.8)

    def test_parses_plain_json(self):
        text = '{"summary": "Bearish setup", "trend": "bearish", "confidence": 0.6}'
        result = parse_ai_reply(text)
        self.assertEqual(result.trend, "bearish")

    def test_null_list_fields_coerced_to_empty(self):
        # gemma-class models sometimes send "key_levels": null etc.
        text = json.dumps({
            "summary": "Weak model, null lists.", "trend": "neutral", "confidence": 0.5,
            "supporting_factors": None, "risk_factors": None,
            "timeframe_conflicts": None, "key_levels": None,
            "trade_plan": {
                "recommendation": "hold", "conviction": "low", "time_horizon": "swing",
                "targets": None,
                "thesis": "Nothing actionable right now, staying flat.",
                "invalidation": "A decisive break either way.",
            },
        })
        result = parse_ai_reply(text)
        self.assertEqual(result.key_levels, [])
        self.assertEqual(result.supporting_factors, [])
        self.assertEqual(result.trade_plan.targets, [])

    def test_parses_balanced_json_with_prose(self):
        # First balanced {...} wins
        text = 'Here is the analysis: {"summary": "Mixed signals.", "trend": "mixed", "confidence": 0.5, "supporting_factors": ["a", "b"], "risk_factors": [], "timeframe_conflicts": ["1d vs 1h"], "key_levels": ["$100"]}. That is all.'
        result = parse_ai_reply(text)
        self.assertEqual(result.trend, "mixed")
        self.assertEqual(result.supporting_factors, ["a", "b"])
        self.assertEqual(result.timeframe_conflicts, ["1d vs 1h"])
        self.assertEqual(result.key_levels, ["$100"])

    def test_rejects_empty_reply(self):
        with self.assertRaises(ValueError):
            parse_ai_reply("")
        with self.assertRaises(ValueError):
            parse_ai_reply(None)
        with self.assertRaises(ValueError):
            parse_ai_reply("   ")

    def test_rejects_no_json(self):
        with self.assertRaises(ValueError):
            parse_ai_reply("This reply contains no JSON at all.")

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            parse_ai_reply("```json\n{not valid json}\n```")

    def test_rejects_missing_required_field(self):
        with self.assertRaises(ValueError):
            parse_ai_reply('{"summary": "x", "trend": "bullish"}')  # no confidence

    def test_rejects_trend_outside_vocabulary(self):
        with self.assertRaises(ValueError):
            parse_ai_reply('{"summary": "x", "trend": "sideways-and-up", "confidence": 0.5}')

    def test_normalizes_engine_vocabulary_synonyms(self):
        """Regression for a live failure (2026-09-09): llama3.2 (Ollama
        fallback) echoed the engine's OWN trend_state.direction vocabulary
        ("downtrend") instead of translating it to the requested output
        vocabulary ("bearish") — a predictable near-miss, not gibberish.
        Known synonyms normalize instead of failing validation; truly
        unrecognized values (test_rejects_trend_outside_vocabulary above)
        still correctly fail."""
        cases = {
            "downtrend": "bearish",
            "uptrend": "bullish",
            "sideways": "neutral",
            "strong_bearish": "bearish",
            "strong_bullish": "bullish",
            "weak_bearish": "bearish",
            "weak_bullish": "bullish",
            "no_signal": "uncertain",
            "unknown": "uncertain",
            "UpTrend": "bullish",  # case-insensitive
            "STRONG-BULLISH": "bullish",  # hyphen -> underscore normalization
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                result = parse_ai_reply(
                    json.dumps({"summary": "x" * 15, "trend": raw, "confidence": 0.5})
                )
                self.assertEqual(result.trend, expected)

    def test_rejects_confidence_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_ai_reply('{"summary": "x", "trend": "bullish", "confidence": 1.5}')

    def test_rejects_summary_too_short(self):
        with self.assertRaises(ValueError):
            parse_ai_reply('{"summary": "short", "trend": "bullish", "confidence": 0.5}')

    def test_strips_blank_strings_from_lists(self):
        text = json.dumps({
            "summary": "ok summary text",
            "trend": "bullish",
            "confidence": 0.5,
            "supporting_factors": ["valid", "", "  "],
            "risk_factors": ["", "another"],
        })
        result = parse_ai_reply(text)
        self.assertEqual(result.supporting_factors, ["valid"])
        self.assertEqual(result.risk_factors, ["another"])

    def test_key_levels_dict_items_rendered_readably(self):
        # A model sometimes sends key_levels as objects instead of
        # strings — must not leak Python's dict repr ("{'price': ...}")
        # into what the UI renders.
        text = json.dumps({
            "summary": "ok summary text",
            "trend": "bullish",
            "confidence": 0.5,
            "key_levels": [
                {"price": 0.2, "type": "support"},
                {"price": 0.21, "type": "resistance"},
                {"level": 5.5, "label": "pivot"},
                {"value": 12},
                {"weird_key": "unrecognized shape"},
                "$100 support",
                242.76,
            ],
        })
        result = parse_ai_reply(text)
        self.assertEqual(result.key_levels, [
            "0.2 support",
            "0.21 resistance",
            "5.5 pivot",
            "12",
            "weird_key: unrecognized shape",
            "$100 support",
            "242.76",
        ])
        for level in result.key_levels:
            self.assertNotIn("{", level)
            self.assertNotIn("'", level)

    def test_caps_list_size(self):
        # The validator's max_length=10 is enforced by Pydantic
        factors = [f"factor {i}" for i in range(20)]
        text = json.dumps({
            "summary": "x" * 20,
            "trend": "bullish",
            "confidence": 0.5,
            "supporting_factors": factors,
        })
        with self.assertRaises(ValueError):
            parse_ai_reply(text)


# --- UncertaintyResponse --------------------------------------------


class TestUncertaintyResponse(unittest.TestCase):

    def test_defaults(self):
        u = UncertaintyResponse(summary="no data")
        self.assertEqual(u.trend, "uncertain")
        self.assertEqual(u.confidence, 0.0)
        self.assertEqual(u.supporting_factors, [])
        self.assertIsNone(u.trade_plan)


# --- TradePlan (advisory layer, 2026-09-10) ------------------------


class TestTradePlan(unittest.TestCase):

    def _base(self, **over):
        from backend.ai.prompt import TradePlan
        kw = dict(
            recommendation="buy", conviction="medium", time_horizon="swing",
            entry_zone_low=100.0, entry_zone_high=102.0, stop_loss=96.0,
            targets=[108.0, 115.0], risk_reward=99.0,
            thesis="Buy the pullback into support with the daily trend up.",
            invalidation="A daily close below 96 breaks the structure.",
        )
        kw.update(over)
        return TradePlan(**kw)

    def test_valid_buy_plan_recomputes_risk_reward(self):
        tp = self._base()
        # entry mid 101, stop 96 -> risk 5; first target 108 -> reward 7
        self.assertAlmostEqual(tp.risk_reward, 1.4, places=2)
        self.assertEqual(tp.targets, [108.0, 115.0])  # sorted ascending

    def test_buy_rejects_stop_above_entry(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._base(stop_loss=101.0)

    def test_buy_rejects_target_below_entry(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._base(targets=[99.0])

    def test_sell_plan_orientation(self):
        tp = self._base(
            recommendation="sell", entry_zone_low=100.0, entry_zone_high=102.0,
            stop_loss=106.0, targets=[95.0, 90.0],
        )
        self.assertEqual(tp.targets, [95.0, 90.0])  # sorted descending
        self.assertGreater(tp.stop_loss, 101.0)

    def test_sell_rejects_stop_below_entry(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._base(recommendation="sell", stop_loss=95.0, targets=[90.0])

    def test_hold_clears_actionable_levels(self):
        tp = self._base(recommendation="hold")
        self.assertIsNone(tp.entry_zone_low)
        self.assertIsNone(tp.stop_loss)
        self.assertEqual(tp.targets, [])
        self.assertIsNone(tp.risk_reward)

    def test_entry_zone_low_high_swapped_is_normalized(self):
        tp = self._base(entry_zone_low=102.0, entry_zone_high=100.0)
        self.assertEqual(tp.entry_zone_low, 100.0)
        self.assertEqual(tp.entry_zone_high, 102.0)

    def test_targets_null_is_treated_as_empty(self):
        # a weak model emitting "targets": null must not fail the whole plan
        tp = self._base(targets=None, risk_reward=None)
        self.assertEqual(tp.targets, [])

    def test_targets_scalar_is_wrapped(self):
        tp = self._base(targets=108.0)
        self.assertEqual(tp.targets, [108.0])

    def test_parsed_from_analysis_reply(self):
        raw = (
            "```json\n" + json.dumps({
                "summary": "AAPL is in a clean uptrend with MTF alignment.",
                "trend": "bullish", "confidence": 0.78,
                "supporting_factors": [], "risk_factors": [],
                "timeframe_conflicts": [], "key_levels": [],
                "trade_plan": {
                    "recommendation": "buy", "conviction": "high",
                    "time_horizon": "position",
                    "entry_zone_low": 314, "entry_zone_high": 316,
                    "stop_loss": 309, "targets": [322, 330],
                    "risk_reward": 5.0,
                    "thesis": "Daily uptrend, buy the dip to support.",
                    "invalidation": "Loss of the 309 shelf on a closing basis.",
                },
            }) + "\n```"
        )
        r = parse_ai_reply(raw)
        self.assertIsNotNone(r.trade_plan)
        self.assertEqual(r.trade_plan.recommendation, "buy")
        # AI's claimed risk_reward (5.0) is overridden by the recompute.
        self.assertNotEqual(r.trade_plan.risk_reward, 5.0)


# --- build_user_prompt ----------------------------------------------


class TestBuildUserPrompt(unittest.TestCase):

    def test_includes_context_in_fence(self):
        ctx = {"symbol": "AAPL", "price": 100.0}
        prompt = build_user_prompt(ctx)
        self.assertIn("<context>", prompt)
        self.assertIn("</context>", prompt)
        self.assertIn("AAPL", prompt)
        # Pretty-printed JSON should include indentation
        self.assertIn("\n", prompt)

    def test_handles_non_serialisable_via_str(self):
        from datetime import datetime
        ctx = {"timestamp": datetime(2026, 1, 1)}
        prompt = build_user_prompt(ctx)
        # default=str serialises datetime to ISO
        self.assertIn("2026-01-01", prompt)


# --- build_context (smoke test) -------------------------------------


class TestBuildContext(unittest.TestCase):

    def test_insufficient_data_for_unknown_symbol_raises(self):
        with self.assertRaises(InsufficientDataError):
            build_context("ZZZZZZ", "1d")

    @patch("backend.ai.context.market_scanner")
    def test_context_for_known_symbol(self, mock_scanner):
        # Provide a ScanResult with trend signals so build_context has something
        # to parse. Without this patch the scanner returns empty signals (fresh
        # TrendEngine with no warmup) and the assertion fails.
        mock_scanner.scan_symbol.return_value = _fake_scan_result()

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.symbol, "AAPL")
        self.assertIsNotNone(ctx.price)
        self.assertGreater(len(ctx.timeframe_scores), 0)
        d = ctx.to_dict()
        # Required keys
        for k in [
            "symbol", "timeframe", "price", "timestamp", "data_status",
            "timeframe_scores", "trend_state", "market_structure",
            "market_regime", "relative_strength", "sector_alignment",
            "volume", "momentum", "support_resistance", "trend_transition",
            "historical_signal_stats",
        ]:
            self.assertIn(k, d)

    def test_context_symbol_uppercased(self):
        ctx = build_context("aapl", "1d")
        self.assertEqual(ctx.symbol, "AAPL")

    def test_compact_drops_empty_fields(self):
        ctx = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t",
            data_status="live", trend_state={"direction": "bullish"},
        )
        compact = ctx.compact()
        self.assertIn("trend_state", compact)
        self.assertEqual(compact["trend_state"], {"direction": "bullish"})
        self.assertNotIn("news", compact)
        self.assertNotIn("tape", compact)
        self.assertNotIn("fundamentals", compact)
        self.assertNotIn("track_record", compact)
        self.assertIn("price", compact)
        self.assertIn("data_status", compact)

    def test_compact_keeps_empty_when_all_fields_populated(self):
        ctx = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t",
            data_status="live", timeframe_scores={"1d": {"direction": "bullish"}},
            trend_state={"direction": "bullish"},
            market_structure={"total_score": 10.0},
            market_regime={"regime": "risk_on"},
            relative_strength={"benchmark": "SPY"},
            sector_alignment={"sector_etf": "XLK"},
            volume={"rvol": 1.2},
            momentum={"rsi": 65.0},
            support_resistance={"supports": [{"price": 99.0}]},
            trend_transition={"direction": "up"},
            historical_signal_stats={"win_rate": 0.5},
            news=[{"headline": "test"}],
            fundamentals={"sector": "Tech"},
            divergence={"type": "bullish"},
            tape={"pressure": 1.0},
            track_record={"total_signals": 5},
        )
        compact = ctx.compact()
        self.assertEqual(len(compact), 21)


# --- O9: scan cache reuse in build_context -----------------------------


class TestBuildContextScanCache(unittest.TestCase):
    """build_context() reuses a fresh cached ScanResult instead of
    calling market_scanner.scan_symbol() — critical for the digest path
    which batch-scans all symbols once."""

    @patch("backend.ai.context.market_scanner")
    def test_reuses_fresh_cached_scan_result(self, mock_scanner):
        from datetime import datetime

        # Fresh scan in the cache (now)
        fresh = _fake_scan_result()
        fresh.timestamp = datetime.now(UTC)
        mock_scanner.get_scan_result.return_value = fresh

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.symbol, "AAPL")
        mock_scanner.scan_symbol.assert_not_called()

    @patch("backend.ai.context.market_scanner")
    def test_stale_cache_triggers_rescan(self, mock_scanner):
        from datetime import datetime, timedelta

        # Stale scan (10 seconds ago, past the 5s TTL)
        stale = _fake_scan_result()
        stale.timestamp = datetime.now(UTC) - timedelta(seconds=10)
        mock_scanner.get_scan_result.return_value = stale
        mock_scanner.scan_symbol.return_value = fresh_result = _fake_scan_result()
        fresh_result.timestamp = datetime.now(UTC)

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.symbol, "AAPL")
        mock_scanner.scan_symbol.assert_called_once_with("AAPL")

    @patch("backend.ai.context.market_scanner")
    def test_no_cache_triggers_scan(self, mock_scanner):
        mock_scanner.get_scan_result.return_value = None
        mock_scanner.scan_symbol.return_value = _fake_scan_result()

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.symbol, "AAPL")
        mock_scanner.scan_symbol.assert_called_once_with("AAPL")


class TestSummarizeContext(unittest.TestCase):
    """summarize_context truncates only safe-to-lose verbose fields."""

    def test_small_context_unchanged(self):
        ctx = {"symbol": "AAPL", "price": 100.0, "trend_state": {"direction": "bullish"}}
        result = summarize_context(ctx)
        self.assertEqual(result, ctx)
        self.assertNotIn("context_summarized", result)

    def test_large_context_truncated(self):
        long_headlines = [{"headline": "X" * 200} for _ in range(130)]
        ctx = {
            "symbol": "AAPL",
            "price": 100.0,
            "trend_state": {"direction": "bullish"},
            "news": long_headlines,
        }
        result = summarize_context(ctx)
        self.assertTrue(result.get("context_summarized"))
        self.assertLessEqual(len(result["news"]), 3)
        for item in result["news"]:
            self.assertLessEqual(len(item["headline"]), 120)

    def test_large_context_capped_fundamentals(self):
        ctx = {
            "symbol": "AAPL",
            "fundamentals": {"sector": "Tech", "industry": "Y" * 20000},
        }
        result = summarize_context(ctx, token_budget=1000)
        self.assertTrue(result.get("context_summarized"))
        self.assertLessEqual(len(result["fundamentals"]["industry"]), 60)

    def test_large_context_capped_signals(self):
        ctx = {
            "symbol": "AAPL",
            "market_structure": {"total_score": 10.0, "signals": [f"signal-{i}-padding" for i in range(500)]},
        }
        result = summarize_context(ctx, token_budget=500)
        self.assertTrue(result.get("context_summarized"))
        self.assertLessEqual(len(result["market_structure"]["signals"]), 5)

    def test_preserves_core_quant_fields(self):
        ctx = {
            "symbol": "AAPL",
            "price": 100.0,
            "timeframe": "1d",
            "trend_state": {"direction": "bullish"},
            "market_regime": {"regime": "risk_on"},
            "volume": {"rvol": 1.2},
            "momentum": {"rsi": 65.0},
            "support_resistance": {"supports": [{"price": 99.0}]},
            "news": [{"headline": "X" * 200} for _ in range(10)],
        }
        result = summarize_context(ctx, token_budget=100)
        for k in ["symbol", "price", "timeframe", "trend_state", "market_regime", "volume", "momentum", "support_resistance"]:
            self.assertIn(k, result)
        self.assertEqual(result["trend_state"], {"direction": "bullish"})

    def test_custom_budget(self):
        ctx = {"symbol": "AAPL", "news": [{"headline": "X" * 200} for _ in range(10)]}
        result = summarize_context(ctx, token_budget=100)
        self.assertTrue(result.get("context_summarized"))
        if "news" in result:
            self.assertLessEqual(len(result["news"]), 3)


# --- O4: short-term analysis cache -------------------------------------


class TestAnalysisCache(unittest.TestCase):
    """analyze_symbol() caches results for the same symbol/timeframe/advisory."""

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_second_call_returns_cached(self, mock_ctx, mock_ai):
        _clear_analysis_cache()
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text='{"summary":"AAPL shows mixed signals across timeframes.","trend":"bullish","confidence":0.5}',
            provider="test", model="test",
        )
        mock_ai.settings = MagicMock(max_tokens=20000)

        r1 = asyncio.run(analyze_symbol("AAPL", "1d"))
        r2 = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertIs(r1.trend, r2.trend)
        # Second call should NOT re-invoke the AI or build_context
        self.assertEqual(mock_ai.complete.call_count, 1)
        self.assertEqual(mock_ctx.call_count, 1)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_different_advisory_not_cached(self, mock_ctx, mock_ai):
        _clear_analysis_cache()
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text='{"summary":"AAPL shows mixed signals across timeframes.","trend":"bullish","confidence":0.5}',
            provider="test", model="test",
        )
        mock_ai.settings = MagicMock(max_tokens=20000)

        asyncio.run(analyze_symbol("AAPL", "1d", advisory=True))
        asyncio.run(analyze_symbol("AAPL", "1d", advisory=False))
        self.assertEqual(mock_ai.complete.call_count, 2)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_system_prompt_override_skips_cache(self, mock_ctx, mock_ai):
        _clear_analysis_cache()
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text='{"summary":"AAPL shows mixed signals across timeframes.","trend":"bullish","confidence":0.5}',
            provider="test", model="test",
        )
        mock_ai.settings = MagicMock(max_tokens=20000)

        asyncio.run(analyze_symbol("AAPL", "1d", system_prompt_override="custom"))
        asyncio.run(analyze_symbol("AAPL", "1d", system_prompt_override="custom"))
        # Both calls bypass cache → two AI calls
        self.assertEqual(mock_ai.complete.call_count, 2)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_uncertainty_result_cached(self, mock_ctx, mock_ai):
        _clear_analysis_cache()
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=None, provider="disabled", model="llama3.2",
        )
        mock_ai.settings = MagicMock(max_tokens=20000)

        asyncio.run(analyze_symbol("AAPL", "1d"))
        asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertEqual(mock_ai.complete.call_count, 1)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_stale_cache_misses(self, mock_ctx, mock_ai):
        _clear_analysis_cache()
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text='{"summary":"AAPL shows mixed signals across timeframes.","trend":"bullish","confidence":0.5}',
            provider="test", model="test",
        )
        mock_ai.settings = MagicMock(max_tokens=20000)

        asyncio.run(analyze_symbol("AAPL", "1d"))

        # Simulate cache expiry by backdating the entry
        from backend.ai.analyze import _analysis_cache, _cache_key, _cache_lock
        key = _cache_key("AAPL", "1d", True, None, None, None, None)
        with _cache_lock:
            old_ts, old_resp = _analysis_cache[key]
            _analysis_cache[key] = (
                old_ts - 999, old_resp,
            )

        asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertEqual(mock_ai.complete.call_count, 2)


class TestCalibrateConfidence(unittest.TestCase):
    """_calibrate_confidence dampens confidence when the AI's historical
    win-rate on a symbol is poor and the sample is large enough."""

    def test_empty_track_record_returns_confidence_unchanged(self):
        self.assertAlmostEqual(_calibrate_confidence(0.9, {}), 0.9)

    def test_insufficient_sample_leaves_unchanged(self):
        tr = {"win_rate": 0.3, "sample_size": 3}
        self.assertAlmostEqual(_calibrate_confidence(0.8, tr), 0.8)

    def test_high_win_rate_leaves_unchanged(self):
        tr = {"win_rate": 0.7, "sample_size": 10}
        self.assertAlmostEqual(_calibrate_confidence(0.9, tr), 0.9)

    def test_poor_win_rate_dampens_toward_neutral(self):
        tr = {"win_rate": 0.3, "sample_size": 20}
        # 0.9 → 0.5 + (0.9 - 0.5) * 0.5 = 0.7
        result = _calibrate_confidence(0.9, tr)
        self.assertAlmostEqual(result, 0.7)

    def test_low_confidence_dampens_upward(self):
        tr = {"win_rate": 0.2, "sample_size": 15}
        # 0.2 → 0.5 + (0.2 - 0.5) * 0.5 = 0.35
        result = _calibrate_confidence(0.2, tr)
        self.assertAlmostEqual(result, 0.35)

    def test_damping_never_exceeds_declared(self):
        tr = {"win_rate": 0.1, "sample_size": 10}
        for c in (0.0, 0.3, 0.5, 0.9, 1.0):
            result = _calibrate_confidence(c, tr)
            self.assertGreaterEqual(result, 0.0)
            self.assertLessEqual(result, 1.0)

    def test_damping_moves_toward_neutral(self):
        """Dampening always pulls confidence halfway toward 0.5."""
        tr = {"win_rate": 0.1, "sample_size": 10}
        # above neutral → goes down
        self.assertLess(_calibrate_confidence(0.9, tr), 0.9)
        self.assertAlmostEqual(_calibrate_confidence(0.9, tr), 0.7)
        # below neutral → goes up (toward 0.5)
        self.assertGreater(_calibrate_confidence(0.2, tr), 0.2)
        self.assertAlmostEqual(_calibrate_confidence(0.2, tr), 0.35)

    def test_damping_never_uses_default_sample_size(self):
        """When sample_size key is missing, default 0 → insufficient → unchanged."""
        tr = {"win_rate": 0.2}
        self.assertAlmostEqual(_calibrate_confidence(0.8, tr), 0.8)


# --- O7: adaptive temperature -----------------------------------------


class TestAdaptiveTemperature(unittest.TestCase):
    """Adaptive temperature picks 0.2 for rich contexts, 0.5 for sparse."""

    def test_sparse_context_gets_higher_temperature(self):
        ctx = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t",
            data_status="live",
        )
        self.assertEqual(_data_quality(ctx), 0.0)
        self.assertEqual(_adaptive_temperature(ctx), 0.5)

    def test_rich_context_gets_lower_temperature(self):
        ctx = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t",
            data_status="live",
            timeframe_scores={"1d": {"direction": "bullish"}},
            trend_state={"direction": "bullish"},
            market_structure={"total_score": 10.0},
            market_regime={"regime": "risk_on"},
            relative_strength={"benchmark": "SPY"},
            sector_alignment={"sector_etf": "XLK"},
            support_resistance={"supports": [{"price": 99.0}]},
            trend_transition={"direction": "up"},
            historical_signal_stats={"win_rate": 0.5},
            news=[{"headline": "test"}],
            fundamentals={"sector": "Tech"},
            divergence={"type": "bullish"},
            tape={"pressure": 1.0},
            track_record={"total_signals": 5},
        )
        self.assertEqual(_data_quality(ctx), 1.0)
        self.assertEqual(_adaptive_temperature(ctx), 0.2)

    def test_moderate_context_gets_higher_temperature(self):
        ctx = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t",
            data_status="live",
            trend_state={"direction": "bullish"},
            market_regime={"regime": "risk_on"},
        )
        quality = _data_quality(ctx)
        self.assertLess(quality, 0.5)
        self.assertEqual(_adaptive_temperature(ctx), 0.5)


# --- I1/I2: confidence calibration + regime-aware prompt --------------


class TestRenderSystemPrompt(unittest.TestCase):
    """render_system_prompt() must inject I1 win-rate calibration and
    I2 regime-aware risk-first framing."""

    def test_no_injections_when_no_data(self):
        result = render_system_prompt(
            "base", track_record={}, market_regime={},
        )
        self.assertEqual(result, "base")

    def test_i1_injects_win_rate_calibration(self):
        tr = {"win_rate": 0.65, "sample_size": 20}
        result = render_system_prompt(
            "base", track_record=tr, market_regime={},
        )
        self.assertIn("historical accuracy on this ticker is 65%", result)
        self.assertIn("20 resolved calls", result)
        self.assertTrue(result.startswith("base\n\n"))

    def test_i1_skips_calibration_when_no_win_rate(self):
        result = render_system_prompt(
            "base", track_record={"sample_size": 5}, market_regime={},
        )
        self.assertEqual(result, "base")

    def test_i1_skips_calibration_when_no_sample_size(self):
        result = render_system_prompt(
            "base", track_record={"win_rate": 0.5}, market_regime={},
        )
        self.assertEqual(result, "base")

    def test_i2_injects_risk_first_for_high_volatility(self):
        mr = {"regime": "high_volatility"}
        result = render_system_prompt(
            "base", track_record={}, market_regime=mr,
        )
        self.assertIn("capital preservation", result)
        self.assertIn("high_volatility", result)

    def test_i2_injects_risk_first_for_crisis(self):
        mr = {"regime": "crisis"}
        result = render_system_prompt(
            "base", track_record={}, market_regime=mr,
        )
        self.assertIn("crisis", result)

    def test_i2_no_injection_for_calm_regime(self):
        mr = {"regime": "risk_on"}
        result = render_system_prompt(
            "base", track_record={}, market_regime=mr,
        )
        self.assertEqual(result, "base")

    def test_i1_and_i2_both_injected(self):
        tr = {"win_rate": 0.3, "sample_size": 10}
        mr = {"regime": "crisis"}
        result = render_system_prompt("base", track_record=tr, market_regime=mr)
        self.assertIn("30%", result)
        self.assertIn("10 resolved calls", result)
        self.assertIn("crisis", result)


class TestHighVolatilityTemperature(unittest.TestCase):
    """I2 lowers temperature further in crisis/high-volatility regimes."""

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_high_vol_caps_temp_via_analyze(self, mock_ctx, mock_ai):
        """In crisis regime, even a rich context gets temp capped at 0.15."""
        rich_ctx = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t",
            data_status="live",
            market_regime={"regime": "crisis"},
            trend_state={"direction": "bullish"},
            market_structure={"total_score": 10.0},
            support_resistance={"supports": [{"price": 99.0}]},
        )
        mock_ctx.return_value = rich_ctx
        mock_ai.complete = AsyncMock(return_value=AIResponse(
            text='{"summary":"AAPL shows mixed signals across timeframes.","trend":"bullish","confidence":0.5}',
            provider="test", model="test",
        ))
        mock_ai.settings = MagicMock(max_tokens=20000)
        asyncio.run(analyze_symbol("AAPL", "1d"))
        temp = mock_ai.complete.call_args.kwargs["temperature"]
        self.assertLessEqual(temp, 0.15)


# --- analyze_symbol end-to-end (mocked) -----------------------------


class TestAnalyzeSymbol(unittest.TestCase):

    def _mocked_analyze(self, **settings_overrides):
        """Patch ``analyze_symbol``-relevant dependencies so the test
        can drive both the context and the AI reply deterministically."""
        pass

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_returns_uncertainty_when_ai_disabled(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live"
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=None, provider="disabled", model="llama3.2"
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertIsInstance(result, UncertaintyResponse)
        self.assertEqual(result.trend, "uncertain")
        self.assertIn("disabled", result.summary)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_returns_uncertainty_when_no_quant_data(self, mock_ctx, mock_ai):
        mock_ctx.side_effect = InsufficientDataError("no quote for ZZZZ")
        # ai_manager is never called
        result = asyncio.run(analyze_symbol("ZZZZ", "1d"))
        self.assertIsInstance(result, UncertaintyResponse)
        self.assertIn("not available", result.summary)
        mock_ai.complete.assert_not_called()

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_returns_uncertainty_when_providers_unavailable(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live"
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=None, provider="none", model="llama3.2"
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertIsInstance(result, UncertaintyResponse)
        self.assertIn("unavailable", result.summary)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_returns_uncertainty_when_reply_fails_to_parse(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live"
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text="Sorry, I can't help with that.", provider="ollama", model="llama3.2"
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertIsInstance(result, UncertaintyResponse)
        self.assertIn("could not be parsed", result.summary)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_returns_validated_response_on_clean_reply(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
            trend_state={"direction": "uptrend", "strength": "strong", "confidence": 0.9},
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=(
                "```json\n"
                + json.dumps({
                    "summary": "AAPL is in a strong uptrend across multiple timeframes.",
                    "trend": "bullish",
                    "confidence": 0.85,
                    "supporting_factors": ["MTF aligned bullish", "above SMA 50"],
                    "risk_factors": ["RSI overbought"],
                    "timeframe_conflicts": [],
                    "key_levels": ["$200 support", "$215 resistance"],
                })
                + "\n```"
            ),
            provider="ollama", model="llama3.2",
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertIsInstance(result, AnalysisResponse)
        self.assertNotIsInstance(result, UncertaintyResponse)
        self.assertEqual(result.trend, "bullish")
        self.assertAlmostEqual(result.confidence, 0.85)
        self.assertEqual(result.supporting_factors, ["MTF aligned bullish", "above SMA 50"])
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.2")

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_reports_the_actual_answering_provider_not_the_configured_primary(
        self, mock_ctx, mock_ai
    ):
        """Regression for a live bug (2026-09-10): analyze_symbol()'s
        result used to carry no provider/model attribution at all —
        every caller (the /analyze endpoint, both background job
        paths) fell back to reporting ai_manager.settings.provider/
        model (the configured PRIMARY) instead, so a fallback-served
        analysis silently claimed to be from the primary. Deliberately
        mocks ai_manager.complete() returning a DIFFERENT provider
        than whatever settings.provider might say, to prove the
        result reflects the real answering provider, not settings."""
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.settings.provider = "openrouter"  # the configured primary
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=(
                "```json\n"
                + json.dumps({
                    "summary": "AAPL is trending up on thin fallback-model reasoning.",
                    "trend": "bullish",
                    "confidence": 0.6,
                    "supporting_factors": [],
                    "risk_factors": [],
                    "timeframe_conflicts": [],
                    "key_levels": [],
                })
                + "\n```"
            ),
            provider="ollama", model="llama3.2",  # the actual fallback that answered
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.2")

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_uncertainty_responses_carry_provider_attribution_too(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text="not json at all", provider="ollama", model="llama3.2",
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertIsInstance(result, UncertaintyResponse)
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.2")

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_passes_max_tokens_and_temperature(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live"
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=None, provider="disabled", model="llama3.2"
        )
        asyncio.run(analyze_symbol("AAPL", "1d", max_tokens=500, temperature=0.5))
        kwargs = mock_ai.complete.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 500)
        self.assertEqual(kwargs["temperature"], 0.5)

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_prompts_contain_system_message(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live"
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=None, provider="disabled", model="llama3.2"
        )
        asyncio.run(analyze_symbol("AAPL", "1d"))
        # System prompt should mention "MarketLens"
        self.assertIn("MarketLens", mock_ai.complete.call_args.kwargs["system"])
        # User prompt should contain the context
        self.assertIn("AAPL", mock_ai.complete.call_args.kwargs["prompt"])
        self.assertIn("<context>", mock_ai.complete.call_args.kwargs["prompt"])

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_ai_trend_disagreement_logs_but_does_not_block(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
            trend_state={"direction": "downtrend", "strength": "strong", "confidence": 0.9},
        )
        # AI says bullish while engine says downtrend
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=(
                "```json\n"
                + json.dumps({
                    "summary": "Despite the engine saying downtrend, I think bullish.",
                    "trend": "bullish",
                    "confidence": 0.4,
                    "supporting_factors": ["x"],
                    "risk_factors": ["y"],
                    "timeframe_conflicts": [],
                    "key_levels": [],
                })
                + "\n```"
            ),
            provider="ollama", model="llama3.2",
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        # Result is still the AI's response — quant truth is separate
        self.assertEqual(result.trend, "bullish")
        # (Logging assertion would need caplog; out of scope for this test)


class TestBareKeyLevelsLabeling(unittest.TestCase):
    """Regression for a live bug (2026-09-11): SYSTEM_PROMPT never told
    the AI key_levels needed a support/resistance label (unlike
    SYSTEM_PROMPT_ANALYST_ONLY, which always has), so replies came back
    as bare numbers ("756.64") with nothing saying which side of price
    they're on. The prompt gap is fixed, but analyze_symbol() also
    labels anything that still slips through as a defense in depth."""

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_bare_numbers_get_labeled_relative_to_price(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=760.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=(
                "```json\n"
                + json.dumps({
                    "summary": "AAPL holding above key support into resistance overhead.",
                    "trend": "bullish",
                    "confidence": 0.7,
                    "supporting_factors": [],
                    "risk_factors": [],
                    "timeframe_conflicts": [],
                    "key_levels": ["756.64", "760.11", "769.7", "779.37", "629.28"],
                })
                + "\n```"
            ),
            provider="ollama", model="llama3.2",
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertEqual(result.key_levels, [
            "756.64 support",   # <= 760.0
            "760.11 resistance",
            "769.7 resistance",
            "779.37 resistance",
            "629.28 support",
        ])

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_already_labeled_levels_are_left_alone(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=760.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=(
                "```json\n"
                + json.dumps({
                    "summary": "AAPL holding above key support into resistance overhead.",
                    "trend": "bullish",
                    "confidence": 0.7,
                    "supporting_factors": [],
                    "risk_factors": [],
                    "timeframe_conflicts": [],
                    "key_levels": ["$756.64 support", "769.7 resistance"],
                })
                + "\n```"
            ),
            provider="ollama", model="llama3.2",
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertEqual(result.key_levels, ["$756.64 support", "769.7 resistance"])

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_no_op_when_price_is_unknown(self, mock_ctx, mock_ai):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=None, timestamp="t", data_status="unknown",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = AIResponse(
            text=(
                "```json\n"
                + json.dumps({
                    "summary": "Cold-start read with no live price available yet.",
                    "trend": "uncertain",
                    "confidence": 0.2,
                    "supporting_factors": [],
                    "risk_factors": [],
                    "timeframe_conflicts": [],
                    "key_levels": ["756.64"],
                })
                + "\n```"
            ),
            provider="ollama", model="llama3.2",
        )
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        self.assertEqual(result.key_levels, ["756.64"])


class TestTradePlanCapture(unittest.TestCase):
    """analyze_symbol()'s single choke point for trade-plan outcome
    tracking (2026-09-11) — see backend.ai.trade_plan_tracker."""

    def _buy_reply(self):
        return AIResponse(
            text="```json\n" + json.dumps({
                "summary": "Clean uptrend, buying the dip to support.",
                "trend": "bullish", "confidence": 0.75,
                "supporting_factors": [], "risk_factors": [],
                "timeframe_conflicts": [], "key_levels": [],
                "trade_plan": {
                    "recommendation": "buy", "conviction": "high", "time_horizon": "swing",
                    "entry_zone_low": 100, "entry_zone_high": 102, "stop_loss": 96,
                    "targets": [108, 115], "risk_reward": 1.4,
                    "thesis": "Daily uptrend, buying the pullback to support.",
                    "invalidation": "A daily close below 96 breaks the structure.",
                },
            }) + "\n```",
            provider="ollama", model="llama3.2",
        )

    def _hold_reply(self):
        return AIResponse(
            text="```json\n" + json.dumps({
                "summary": "Range-bound, nothing actionable right now.",
                "trend": "neutral", "confidence": 0.4,
                "supporting_factors": [], "risk_factors": [],
                "timeframe_conflicts": [], "key_levels": [],
                "trade_plan": {
                    "recommendation": "hold", "conviction": "low", "time_horizon": "swing",
                    "thesis": "No clean setup — staying flat until it breaks the range.",
                    "invalidation": "A decisive break either way.",
                },
            }) + "\n```",
            provider="ollama", model="llama3.2",
        )

    @patch("backend.ai.trade_plan_tracker.record_trade_plan")
    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_buy_plan_is_captured(self, mock_ctx, mock_ai, mock_record):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = self._buy_reply()
        result = asyncio.run(analyze_symbol("AAPL", "1d"))
        mock_record.assert_called_once()
        args, _ = mock_record.call_args
        self.assertEqual(args[0], "AAPL")
        self.assertIs(args[1], result)

    @patch("backend.ai.trade_plan_tracker.record_trade_plan")
    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_hold_plan_is_not_specially_skipped_here(self, mock_ctx, mock_ai, mock_record):
        # analyze_symbol always calls record_trade_plan when there's a
        # trade_plan at all — record_trade_plan itself is what filters
        # hold/avoid (see TestRecordTradePlan in test_trade_plan_tracker.py).
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = self._hold_reply()
        asyncio.run(analyze_symbol("AAPL", "1d"))
        mock_record.assert_called_once()

    @patch("backend.ai.trade_plan_tracker.record_trade_plan")
    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_capture_failure_never_surfaces_as_an_analysis_failure(
        self, mock_ctx, mock_ai, mock_record,
    ):
        mock_ctx.return_value = AnalysisContext(
            symbol="AAPL", timeframe="1d", price=100.0, timestamp="t", data_status="live",
        )
        mock_ai.complete = AsyncMock()
        mock_ai.complete.return_value = self._buy_reply()
        mock_record.side_effect = RuntimeError("db is down")
        result = asyncio.run(analyze_symbol("AAPL", "1d"))  # must not raise
        self.assertEqual(result.trend, "bullish")
        self.assertIsNotNone(result.trade_plan)


# --- Spec compliance: no AI-side indicator calc ----------------------


class TestNoIndicatorRecalculation(unittest.TestCase):
    """Spec: 'AI must NEVER directly calculate raw indicators if the
    application already has the calculation.'"""

    def test_prompt_does_not_ask_for_indicator_calc(self):
        # The prompt should explicitly tell the AI not to calculate
        # indicators — assert on the direct rule rather than word
        # presence (the word "compute" appears in the past tense when
        # describing what the engine has already done).
        lower = SYSTEM_PROMPT.lower()
        self.assertNotIn("you calculate", lower)
        self.assertNotIn("you compute", lower)
        self.assertNotIn("please calculate", lower)
        self.assertNotIn("please compute", lower)
        self.assertIn("never", lower.lower())

    def test_prompt_uses_phrase_never_invent(self):
        self.assertIn("do not invent", SYSTEM_PROMPT.lower())

    def test_advisory_prompt_produces_a_trade_plan(self):
        # 2026-09-10: MarketLens moved from analyst-only to analyst +
        # advisor. The default SYSTEM_PROMPT now asks for a trade_plan;
        # SYSTEM_PROMPT_ANALYST_ONLY (used by the digest) still doesn't.
        from backend.ai.prompt import SYSTEM_PROMPT_ANALYST_ONLY

        self.assertIn("trade_plan", SYSTEM_PROMPT.lower())
        self.assertIn("recommendation", SYSTEM_PROMPT.lower())
        self.assertNotIn("trade_plan", SYSTEM_PROMPT_ANALYST_ONLY.lower())
        self.assertIn("do not issue a trade plan", SYSTEM_PROMPT_ANALYST_ONLY.lower())

    def test_both_prompts_still_forbid_overriding_engine_numbers(self):
        # The advisory shift did NOT relax the "engine's quant numbers
        # are ground truth" rule — the AI proposes entry/stop/target
        # PRICES, never recomputes trend/confidence/indicators.
        from backend.ai.prompt import SYSTEM_PROMPT_ANALYST_ONLY

        for p in (SYSTEM_PROMPT, SYSTEM_PROMPT_ANALYST_ONLY):
            lower = p.lower()
            self.assertIn("never compute", lower)
            self.assertIn("never override", lower)


# --- O11: Structured output schema + response parsing -------------------


class TestStructuredOutputSchema(unittest.TestCase):
    """O11: make_analysis_response_format() returns a valid OpenAI response_format."""

    def test_returns_json_object_type(self):
        fmt = make_analysis_response_format()
        self.assertEqual(fmt["type"], "json_object")

    def test_includes_schema_with_required_fields(self):
        fmt = make_analysis_response_format()
        schema = fmt["json_schema"]
        self.assertIn("required", schema["parameters"])
        self.assertIn("summary", schema["parameters"]["required"])
        self.assertIn("trend", schema["parameters"]["required"])
        self.assertIn("confidence", schema["parameters"]["required"])

    def test_schema_properties_match_analysis_response(self):
        fmt = make_analysis_response_format()
        props = fmt["json_schema"]["parameters"]["properties"]
        self.assertIn("summary", props)
        self.assertIn("trend", props)
        self.assertIn("confidence", props)
        self.assertIn("supporting_factors", props)
        self.assertIn("risk_factors", props)
        self.assertIn("timeframe_conflicts", props)
        self.assertIn("key_levels", props)

    def test_trend_enum_matches_trend_label(self):
        fmt = make_analysis_response_format()
        trend_values = set(fmt["json_schema"]["parameters"]["properties"]["trend"]["enum"])
        # At minimum the basic trend labels must be present
        for basic in ("bullish", "bearish", "mixed", "uncertain"):
            self.assertIn(basic, trend_values)


class TestParseStructuredReply(unittest.TestCase):
    """parse_ai_reply with structured=True skips regex extraction."""

    def test_structured_reply_parsed_directly(self):
        text = '{"summary": "Test analysis text here", "trend": "bullish", "confidence": 0.8}'
        result = parse_ai_reply(text, structured=True)
        self.assertIsInstance(result, AnalysisResponse)
        self.assertEqual(result.trend, "bullish")
        self.assertEqual(result.confidence, 0.8)

    def test_structured_reply_strips_markdown_fences(self):
        text = '```json\n{"summary": "Test analysis text here", "trend": "bearish", "confidence": 0.3}\n```'
        result = parse_ai_reply(text, structured=True)
        self.assertEqual(result.trend, "bearish")
        self.assertEqual(result.confidence, 0.3)

    def test_structured_fallback_to_regex_on_wrapping_text(self):
        # Even in structured mode, if the provider wrapped the JSON in
        # extra prose, we fall back to extract_json_object.
        text = 'Here is the result:\n{"summary": "Test analysis here", "trend": "mixed", "confidence": 0.5}\nThanks!'
        result = parse_ai_reply(text, structured=True)
        self.assertIsInstance(result, AnalysisResponse)
        self.assertEqual(result.trend, "mixed")

    def test_unstructured_uses_regex_extraction(self):
        text = 'Some prose\n{"summary": "Test analysis here", "trend": "bullish", "confidence": 0.9}\nmore prose'
        result = parse_ai_reply(text, structured=False)
        self.assertEqual(result.trend, "bullish")

    def test_structured_invalid_json_falls_back(self):
        # malformed JSON that regex also can't parse → ValueError
        text = "not json at all"
        with self.assertRaises(ValueError):
            parse_ai_reply(text, structured=True)


class TestAnalyzeStructuredOutput(unittest.TestCase):
    """End-to-end: analyze_symbol() passes response_format when supported."""

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_passes_response_format_when_supported(self, mock_build_ctx, mock_ai):
        """When primary supports structured output, response_format is passed."""
        ctx = build_context("AAPL")
        mock_build_ctx.return_value = ctx
        mock_ai.primary_supports_structured_output.return_value = True
        mock_ai.complete = AsyncMock(return_value=AIResponse(
            text='{"summary": "Test analysis here", "trend": "bullish", "confidence": 0.7}',
            provider="test_provider",
            model="test_model",
            structured=True,
        ))
        asyncio.run(analyze_symbol("AAPL"))
        call_kwargs = mock_ai.complete.call_args
        self.assertIsNotNone(call_kwargs.kwargs.get("response_format"))
        self.assertIn("json_schema", call_kwargs.kwargs["response_format"])

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_omits_response_format_when_unsupported(self, mock_build_ctx, mock_ai):
        """When primary doesn't support structured output, response_format is None."""
        ctx = build_context("AAPL")
        mock_build_ctx.return_value = ctx
        mock_ai.primary_supports_structured_output.return_value = False
        mock_ai.complete = AsyncMock(return_value=AIResponse(
            text='{"summary": "Test analysis here", "trend": "bullish", "confidence": 0.7}',
            provider="test_provider",
            model="test_model",
            structured=False,
        ))
        asyncio.run(analyze_symbol("AAPL"))
        call_kwargs = mock_ai.complete.call_args
        self.assertIsNone(call_kwargs.kwargs.get("response_format"))

    @patch("backend.ai.analyze.ai_manager")
    @patch("backend.ai.analyze.build_context")
    def test_structured_flag_propagated_to_parse(self, mock_build_ctx, mock_ai):
        """The structured flag from the response is passed to parse_ai_reply."""
        ctx = build_context("AAPL")
        mock_build_ctx.return_value = ctx
        mock_ai.primary_supports_structured_output.return_value = True
        mock_ai.complete = AsyncMock(return_value=AIResponse(
            text='{"summary": "Test analysis here", "trend": "bullish", "confidence": 0.9}',
            provider="test_provider",
            model="test_model",
            structured=True,
        ))
        with patch("backend.ai.analyze.parse_ai_reply") as mock_parse:
            mock_parse.return_value = AnalysisResponse(
                summary="Test analysis here",
                trend="bullish",
                confidence=0.9,
            )
            asyncio.run(analyze_symbol("AAPL"))
            mock_parse.assert_called_once()
            args = mock_parse.call_args
            self.assertEqual(args.kwargs.get("structured"), True)


# --- O10: multi-symbol correlation context -------------------------------


class TestCorrelationContext(unittest.TestCase):
    """O10 — build_context() can scan peer symbols and summarize their
    trend direction so the AI can reason about cross-ticker
    confluence/divergence instead of analyzing each ticker in isolation."""

    @patch("backend.ai.context.market_scanner")
    def test_no_peers_returns_empty(self, mock_scanner):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.correlation_context, {})

    @patch("backend.ai.context.market_scanner")
    def test_peers_summarized(self, mock_scanner):
        peer = _fake_scan_result()
        mock_scanner.scan_symbol.return_value = peer
        ctx = build_context("AAPL", "1d", portfolio_symbols=["MSFT", "GOOG"])
        self.assertEqual(ctx.correlation_context["peer_count"], 2)
        self.assertGreaterEqual(ctx.correlation_context["aligned"], 1)
        self.assertEqual(len(ctx.correlation_context["peers"]), 2)

    @patch("backend.ai.context.market_scanner")
    def test_self_excluded_from_peers(self, mock_scanner):
        peer = _fake_scan_result()
        mock_scanner.scan_symbol.return_value = peer
        ctx = build_context("AAPL", "1d", portfolio_symbols=["AAPL", "MSFT"])
        # AAPL is the subject (scanned once as primary), MSFT is the
        # only peer — AAPL must NOT be re-scanned as a peer.
        self.assertEqual(ctx.correlation_context["peer_count"], 1)
        called_syms = [call.args[0] for call in mock_scanner.scan_symbol.call_args_list]
        self.assertIn("MSFT", called_syms)
        # AAPL is called once (as the primary scan), not twice (as a peer)
        self.assertEqual(called_syms.count("AAPL"), 1)

    def test_render_system_prompt_injects_correlation(self):
        from backend.ai.prompt import render_system_prompt

        base = "You are an analyst."
        prompt = render_system_prompt(
            base,
            track_record={},
            market_regime={},
            correlation_context={
                "peer_count": 5,
                "aligned": 3,
                "opposed": 1,
                "primary_sector": "Technology",
            },
        )
        self.assertIn("Portfolio/sector peers", prompt)
        self.assertIn("Technology", prompt)
        self.assertIn("3 bullish", prompt)
        self.assertIn("1 bearish", prompt)

    def test_render_system_prompt_skips_when_empty(self):
        from backend.ai.prompt import render_system_prompt

        base = "You are an analyst."
        prompt = render_system_prompt(
            base,
            track_record={},
            market_regime={},
            correlation_context={},
        )
        self.assertEqual(prompt, base)


if __name__ == "__main__":
    unittest.main()
