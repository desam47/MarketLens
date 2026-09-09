"""
Performance benchmarks for the market scanner.

These tests use pytest-benchmark. They are skipped by default during the regular
test run (``pytest tests/``) and only run when invoked explicitly:

    pytest tests/benchmarks/ --benchmark-enable --benchmark-only
    pytest tests/benchmarks/ --benchmark-enable --benchmark-compare=baseline

The fixture ``fresh_scanner`` replaces the live YFinance call with a
deterministic stub so the benchmark measures the in-process scan pipeline
(per-symbol: trend engine update + signals), not network I/O.

A note on the numbers: the stub returns instantly, so the ``scan_symbols_async``
benchmark does NOT show a speedup over the sync version. ``asyncio.to_thread``
has fixed overhead per task (~0.5–1ms scheduling + GIL contention), and with
microsecond-scale per-symbol work the overhead dominates.

The real-world win is when ``scan_symbol`` makes a network call that takes
100–300ms (the yfinance quote fetch). With 30 symbols:

  sync:   30 × 200ms = 6.0s
  async:  max(200ms) × ceil(30 / workers) ≈ 600ms   (10x speedup)

A network-aware benchmark using ``asyncio.sleep`` to simulate HTTP latency
would demonstrate that — it's omitted here to keep the benchmark hermetic
and runnable offline. Run the dashboard or any batch scan endpoint in dev
to observe the wall-clock difference.

Run ``--benchmark-save=baseline`` first, then again after the optimization, and
``--benchmark-compare=baseline`` to see the delta.
"""
import asyncio
import os
import sys
from datetime import datetime
from unittest.mock import patch

import pytest

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.models.market_data import DataStatus, Quote
from backend.scanner.scanner import Scanner

SAMPLE_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "AMZN", "META", "NFLX", "AMD", "INTC",
    "JPM", "BAC", "WFC", "GS", "MS", "XOM", "CVX", "PFE", "JNJ", "UNH",
    "HD", "LOW", "MCD", "SBUX", "NKE", "DIS", "CMCSA", "T", "VZ", "KO",
]

pytestmark = pytest.mark.benchmark


@pytest.fixture
def fresh_scanner():
    """A fresh Scanner with a stubbed market_data_manager."""
    with patch('backend.scanner.scanner.market_data_manager') as mock:
        def get_quote_side_effect(symbol):
            return Quote(
                symbol=symbol,
                price=100.0 + (hash(symbol) % 50),
                timestamp=datetime.now(),
                provider="yahoo_finance",
                data_status=DataStatus.DELAYED,
                volume=1_000_000,
            )
        mock.get_quote.side_effect = get_quote_side_effect
        yield Scanner()


def test_scan_symbols_sync(fresh_scanner, benchmark):
    """Baseline: sequential ``scan_symbols`` over 30 symbols."""
    result = benchmark(fresh_scanner.scan_symbols, SAMPLE_SYMBOLS)
    assert len(result) == len(SAMPLE_SYMBOLS)


def test_scan_symbols_async(fresh_scanner, benchmark):
    """Async parallelized version: ``scan_symbols_async`` over 30 symbols.

    With an instant stub the per-task overhead of ``asyncio.to_thread`` makes
    this slightly slower than the sync path. The win only shows when the
    per-symbol work is dominated by blocking I/O (real YFinance calls).
    """
    async def _run():
        return await fresh_scanner.scan_symbols_async(SAMPLE_SYMBOLS)

    result = benchmark(lambda: asyncio.run(_run()))
    assert len(result) == len(SAMPLE_SYMBOLS)


def test_scan_symbols_async_with_simulated_latency(fresh_scanner, benchmark):
    """Async version with a 50ms simulated network call per symbol.

    This is the realistic comparison: 30 symbols × 50ms = 1500ms sync vs
    ~50ms × ceil(30 / workers) async. The benchmark makes the parallelism
    win visible without needing a real network.
    """
    async def _slow_quote(symbol):
        await asyncio.sleep(0.05)  # 50ms simulated HTTP round-trip
        return Quote(
            symbol=symbol,
            price=100.0 + (hash(symbol) % 50),
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
            volume=1_000_000,
        )

    # patch the scanner's scan_symbol to use the slow stub
    original_scan = fresh_scanner.scan_symbol

    def slow_scan(symbol, historical_bars=None, quote=None):
        # scan_symbols_async batch-pre-fetches bars/quotes and passes them
        # into scan_symbol per-symbol — accept (and ignore) those extra
        # params so this stub matches the real signature.
        return asyncio.run(_slow_quote(symbol))

    fresh_scanner.scan_symbol = slow_scan
    try:
        async def _run():
            return await fresh_scanner.scan_symbols_async(SAMPLE_SYMBOLS)
        result = benchmark(lambda: asyncio.run(_run()))
        assert len(result) == len(SAMPLE_SYMBOLS)
    finally:
        fresh_scanner.scan_symbol = original_scan


