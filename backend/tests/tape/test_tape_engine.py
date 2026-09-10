"""
Tests for backend.tape.tape_engine.TapeEngine — feed synthetic prints,
assert the derived snapshot (pattern from test_trend_engine.py).
"""
import time
import unittest

from backend.tape.tape_engine import TapeEngine


def _feed(engine, base, specs):
    """specs: list of (offset_s, price, size, side_or_None)."""
    for off, price, size, side in specs:
        engine.update(price=price, size=size, timestamp=base + off, side=side)


class TestTapeEngine(unittest.TestCase):
    def test_signed_volume_and_buy_ratio(self):
        e = TapeEngine("AAPL")
        base = time.time() - 20
        _feed(e, base, [(i * 0.4, 100 + i * 0.01, 200, "buy" if i % 5 else "sell")
                        for i in range(50)])
        snap = e.get_snapshot(now_s=base + 20)
        self.assertGreater(snap["signed_volume"], 0)
        self.assertGreater(snap["buy_ratio"], 0.7)
        self.assertEqual(snap["buy_volume"] - snap["sell_volume"], snap["signed_volume"])
        self.assertEqual(snap["pressure"], "heavy_buy")

    def test_heavy_sell_pressure(self):
        e = TapeEngine("AAPL")
        base = time.time() - 20
        _feed(e, base, [(i * 0.4, 100 - i * 0.01, 200, "sell" if i % 6 else "buy")
                        for i in range(48)])
        snap = e.get_snapshot(now_s=base + 20)
        self.assertLess(snap["signed_volume"], 0)
        self.assertEqual(snap["pressure"], "heavy_sell")

    def test_neutral_pressure_when_balanced(self):
        e = TapeEngine("AAPL")
        base = time.time() - 20
        _feed(e, base, [(i * 0.4, 100, 100, "buy" if i % 2 else "sell") for i in range(40)])
        self.assertEqual(e.get_snapshot(now_s=base + 20)["pressure"], "neutral")

    def test_tick_rule_side_fallback(self):
        e = TapeEngine("X")
        base = time.time() - 10
        # up moves -> buy, down moves -> sell; first defaults buy
        _feed(e, base, [(0, 10.0, 100, None), (1, 10.1, 100, None), (2, 10.2, 100, None),
                        (3, 10.1, 100, None), (4, 10.0, 100, None), (5, 9.9, 100, None)])
        snap = e.get_snapshot(now_s=base + 6)
        # 3 up/flat-first (buy) vs 3 down (sell)
        self.assertEqual(snap["buy_volume"], 300)
        self.assertEqual(snap["sell_volume"], 300)

    def test_block_detection(self):
        e = TapeEngine("AAPL")
        base = time.time() - 10
        _feed(e, base, [(1, 100.0, 100, "buy"), (2, 100.0, 20000, "buy"), (3, 100.0, 50, "sell")])
        snap = e.get_snapshot(now_s=base + 10)
        self.assertEqual(snap["block_count_5m"], 1)  # only the 20k-share print
        self.assertIsNotNone(snap["last_block"])
        self.assertEqual(snap["last_block"]["size"], 20000)

    def test_tape_speed_and_accel(self):
        e = TapeEngine("AAPL")
        base = time.time() - 60
        # slow for 45s, then a burst in the last 15s
        _feed(e, base, [(i * 3.0, 100, 100, "buy") for i in range(15)])
        _feed(e, base, [(45 + i * 0.5, 100, 100, "buy") for i in range(28)])
        snap = e.get_snapshot(now_s=base + 60)
        self.assertGreater(snap["tape_speed"], 0)
        self.assertIsNotNone(snap["tape_accel"])
        self.assertGreater(snap["tape_accel"], 1.0)  # recent faster than the minute avg

    def test_empty_engine_snapshot_is_safe(self):
        snap = TapeEngine("AAPL").get_snapshot()
        self.assertEqual(snap["pressure"], "neutral")
        self.assertEqual(snap["trade_count"], 0)
        self.assertIsNone(snap["buy_ratio"])
        self.assertIsNone(snap["last_block"])

    def test_drain_pending_yields_one_second_bars(self):
        e = TapeEngine("AAPL")
        base = time.time() - 10
        _feed(e, base, [(i * 0.3, 100 + i * 0.01, 100, "buy") for i in range(30)])
        e.update(price=101, size=100, timestamp=base + 11, side="buy")  # roll the last bucket
        bars = e.drain_pending()
        self.assertGreater(len(bars), 3)
        b0 = bars[0]
        self.assertEqual(b0["symbol"], "AAPL")
        self.assertIn("signed_volume", b0)
        self.assertEqual(b0["signed_volume"], b0["buy_volume"] - b0["sell_volume"])
        self.assertEqual(e.drain_pending(), [])  # drained

    def test_bad_input_dropped_not_raised(self):
        e = TapeEngine("AAPL")
        e.update(price=None, size=100, timestamp=time.time())
        e.update(price="notanumber", size=1, timestamp=time.time())
        self.assertEqual(e.get_snapshot()["trade_count"], 0)

    def test_note_price_updates_last_price_without_a_print(self):
        e = TapeEngine("AAPL")
        base = time.time() - 10
        e.update(price=100.0, size=200, timestamp=base, side="buy")
        e.note_price(101.5, base + 5)          # L1 snapshot, no trade
        snap = e.get_snapshot(now_s=base + 6)
        self.assertEqual(snap["last_price"], 101.5)   # reflects the snapshot
        self.assertEqual(snap["trade_count"], 1)      # still just the one real print
        self.assertEqual(snap["buy_volume"], 200)
        self.assertEqual(snap["sell_volume"], 0)
        e.note_price("bad", base + 6)                  # ignored, no raise
        self.assertEqual(e.get_snapshot(now_s=base + 7)["last_price"], 101.5)


if __name__ == "__main__":
    unittest.main()
