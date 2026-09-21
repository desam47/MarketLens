"""Tests for backend.ai.reply_stream.ReplyExtractor — incremental
decode of the JSON "reply" value from a growing raw completion."""

import unittest

from backend.ai.reply_stream import ReplyExtractor


def _stream(chunks):
    """Feed chunks cumulatively (the caller accumulates raw); return the
    concatenated deltas and the extractor."""
    ex = ReplyExtractor()
    raw = ""
    deltas = []
    for c in chunks:
        raw += c
        deltas.append(ex.feed(raw))
    return "".join(deltas), ex


class TestReplyExtractor(unittest.TestCase):
    def test_skips_fence_and_leading_prose(self):
        out, ex = _stream(['Here you go:\n```json\n{"reply": "Hello world"', "}\n```"])
        self.assertEqual(out, "Hello world")
        self.assertTrue(ex.finished)
        self.assertEqual(ex.text, "Hello world")

    def test_streams_incrementally(self):
        out, _ = _stream(['{"reply": "NVDA is ', "showing ", 'strength"}'])
        self.assertEqual(out, "NVDA is showing strength")

    def test_decodes_simple_escapes(self):
        out, _ = _stream([r'{"reply": "line one\nline \"two\" \\ done"}'])
        self.assertEqual(out, 'line one\nline "two" \\ done')

    def test_decodes_unicode_escape(self):
        out, _ = _stream([r'{"reply": "up ↑ arrow"}'])
        self.assertEqual(out, "up ↑ arrow")

    def test_chunk_boundary_mid_escape(self):
        # backslash arrives, then 'n' in the next chunk
        out, _ = _stream(['{"reply": "a\\', 'nb"}'])
        self.assertEqual(out, "a\nb")

    def test_chunk_boundary_mid_unicode(self):
        out, _ = _stream(['{"reply": "x \\u21', '91 y"}'])
        self.assertEqual(out, "x ↑ y")

    def test_closing_quote_marks_finished_and_ignores_trailing(self):
        out, ex = _stream(['{"reply": "done", "grounded": true, "wants_reanalysis": false}'])
        self.assertEqual(out, "done")
        self.assertTrue(ex.finished)
        # further feeds are inert
        self.assertEqual(ex.feed('{"reply": "done", ...} extra'), "")

    def test_no_reply_key_yet(self):
        out, ex = _stream(['{"grounded": true, ', '"othe'])
        self.assertEqual(out, "")
        self.assertFalse(ex.finished)

    def test_whitespace_variation_in_key(self):
        out, _ = _stream(['{"reply"  :   "spaced"}'])
        self.assertEqual(out, "spaced")


if __name__ == "__main__":
    unittest.main()
