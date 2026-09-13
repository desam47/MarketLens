"""
Shared TapeEngine registry — one warmed engine per symbol, fed by the
Webull trade-tick stream via ``engine_registry`` (kind ``"trade"``).

Mirrors ``backend/api/trend/registry.py``: lazy construct on first
``get_tape_engine(symbol)``, best-effort seed from Webull's historical
Time & Sales (``market_data.get_tick``), then register for live ticks.
``warmup_tape_engines()`` pre-warms the watchlist at startup.
"""
from __future__ import annotations

import logging
import threading
import time

from backend.config.settings import settings
from backend.market_data.services.engine_seeder import engine_registry
from backend.tape.tape_engine import TapeEngine

logger = logging.getLogger(__name__)

_engines: dict[str, TapeEngine] = {}

# Background persistence: drain every engine's closed 1-second bars into
# ``tape_bars`` on a timer, so the read paths stay pure and memory is
# bounded regardless of API traffic.
_flush_thread: threading.Thread | None = None
_flush_stop = threading.Event()
_FLUSH_INTERVAL = 5.0
_RETENTION_EVERY = 300.0  # prune once every ~5 min


def _seed_from_webull_ticks(symbol: str, engine: TapeEngine) -> int:
    """Replay up to ~200 recent prints so the engine isn't stone cold.

    Best-effort — a failure just means the engine warms from the live
    stream over the next minute.
    """
    try:
        from backend.market_data.services.manager import get_cached_provider

        provider = get_cached_provider("webull")
        if provider is None:
            return 0
        resp = provider._data_client.market_data.get_tick(symbol, "US_STOCK", count="200")
        if getattr(resp, "status_code", 200) != 200:
            return 0
        rows = resp.json()
        if not isinstance(rows, list):
            return 0
    except Exception as e:  # noqa: BLE001
        logger.debug("tape seed failed for %s: %s", symbol, e)
        return 0

    prints = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        price = r.get("price") or r.get("deal_price")
        size = r.get("volume") or r.get("size") or r.get("trade_volume")
        ts = r.get("trade_time") or r.get("timestamp") or r.get("time")
        raw_side = str(r.get("side") or r.get("direction") or "").lower()
        side = "buy" if raw_side in ("1", "buy", "b") else "sell" if raw_side in ("2", "sell", "s") else None
        if price is None:
            continue
        prints.append((ts, float(price), int(size or 0), side))
    prints.sort(key=lambda p: (p[0] or 0))
    engine.seed(prints)
    return len(prints)


def get_tape_engine(symbol: str) -> TapeEngine:
    """Get or create the shared TapeEngine for ``symbol``.

    The historical-seed replay (Webull Time & Sales) used to run inline
    here, blocking the request on a synchronous SDK network call that can
    take 5-15s (or hang until the SDK's own timeout) — so the very first
    ``GET /api/tape/{symbol}`` would time out. The engine is now returned
    immediately and the seed runs in a daemon thread, populating history
    in the background. ``get_snapshot()`` works from the live stream in
    the meantime, so the endpoint never blocks on the seed.
    """
    symbol = symbol.upper()
    if symbol not in _engines:
        engine = TapeEngine(symbol)
        _engines[symbol] = engine
        engine_registry.register("trade", symbol, engine.update)
        threading.Thread(
            target=_seed_from_webull_ticks,
            args=(symbol, engine),
            name=f"tape-seed-{symbol}",
            daemon=True,
        ).start()
        logger.info("Tape engine ready for %s (seeding in background)", symbol)
    return _engines[symbol]


def has_tape_engine(symbol: str) -> bool:
    return symbol.upper() in _engines


def all_tape_engines() -> dict[str, TapeEngine]:
    return dict(_engines)


def _persist_once() -> int:
    """Drain + upsert every engine's pending 1-second bars. Never raises."""
    rows: list[dict] = []
    for eng in list(_engines.values()):
        try:
            rows.extend(eng.drain_pending())
        except Exception:  # noqa: BLE001
            pass
    if not rows:
        return 0
    try:
        from backend.database import SessionLocal
        from backend.repositories.tape_repository import upsert_tape_bars

        db = SessionLocal()
        try:
            return upsert_tape_bars(db, rows)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("tape persist failed (%d rows dropped): %s", len(rows), e)
        return 0


def _prune_once() -> None:
    try:
        from datetime import timedelta

        from backend.database import SessionLocal
        from backend.repositories.tape_repository import prune_tape_bars
        from backend.utils.timezone import now_ny

        cutoff = now_ny() - timedelta(days=settings.tape.retention_days)
        db = SessionLocal()
        try:
            n = prune_tape_bars(db, cutoff)
            if n:
                logger.info("tape retention: pruned %d rows older than %s", n, cutoff.date())
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("tape prune failed: %s", e)


def _flush_loop() -> None:
    last_prune = 0.0
    while not _flush_stop.wait(_FLUSH_INTERVAL):
        _persist_once()
        now = time.monotonic()
        if now - last_prune > _RETENTION_EVERY:
            _prune_once()
            last_prune = now


def start_tape_flusher() -> None:
    global _flush_thread
    if _flush_thread is not None and _flush_thread.is_alive():
        return
    _flush_stop.clear()
    _flush_thread = threading.Thread(target=_flush_loop, name="tape-flush", daemon=True)
    _flush_thread.start()


def stop_tape_flusher() -> None:
    _flush_stop.set()
    _persist_once()  # final drain


def warmup_tape_engines() -> list[str]:
    """Pre-create tape engines for every ingested symbol and start the
    background flusher. Called from the lifespan hook when
    ``settings.tape.enabled``."""
    if not settings.tape.enabled:
        return []
    try:
        from backend.market_data.services.ingestion_service import ingestion_service

        symbols = list(ingestion_service.symbols)
    except Exception:  # noqa: BLE001
        symbols = []
    for sym in symbols:
        try:
            get_tape_engine(sym)
        except Exception as e:  # noqa: BLE001
            logger.warning("tape warmup failed for %s: %s", sym, e)
    start_tape_flusher()
    return symbols
