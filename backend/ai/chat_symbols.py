"""
Ticker resolution for the universal AI Hub chat (2026-09-10).

Turns one free-text chat message into an ordered list of tickers to
build quant context for. Deterministic first — regex + a stopword
denylist + a known-symbol set — then one batched live-quote lookup to
validate anything unknown, then (optionally) one small AI call to map a
bare company name to a ticker.

Nothing here raises: every fallible step degrades to "no symbol", and
the caller (backend.ai.chat.answer_chat_message) always attaches a
market-wide baseline regardless, so a 0-symbol turn is still answerable.

Nothing in backend/nl_search/ is reusable — that layer produces
screening filters, never a ticker list.
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_VALID_CACHE: "OrderedDict[str, bool]" = OrderedDict()  # token -> True (hits only)
_VALID_CACHE_CAP = 256

# Known-symbol set, rebuilt lazily every ~60s so a freshly added
# watchlist ticker starts being recognised without a restart.
_known_cache: set[str] = set()
_known_built_at: float = 0.0
_KNOWN_TTL = 60.0

# Common English words / finance abbreviations that pass the bare
# uppercase-token shape but are never what the trader means. Real
# tickers among these (IT, ALL, ON, F, C, ...) are only accepted via a
# cashtag or an explicit "(TICKER)" / "ticker:" form, or the known set.
_CHAT_STOPWORDS = {
    "A", "I", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN", "IS", "IT",
    "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "ALL", "AND", "ANY", "ARE", "BUT", "CAN", "DAY", "DID", "FOR", "GET", "GOT", "HAS",
    "HAD", "HER", "HIM", "HIS", "HOW", "ITS", "LET", "LOW", "MAY", "NEW", "NOT", "NOW",
    "OFF", "OLD", "ONE", "OUR", "OUT", "OWN", "PER", "PUT", "SEE", "SHE", "THE", "TOO",
    "TOP", "TWO", "USE", "WAS", "WAY", "WHO", "WHY", "YES", "YET", "YOU",
    "BEEN", "BOTH", "DOES", "DOWN", "EACH", "ELSE", "EVEN", "EVER", "FROM", "HAVE",
    "HERE", "INTO", "JUST", "LESS", "LIKE", "MORE", "MOST", "MUCH", "ONLY", "OVER",
    "SOME", "SUCH", "THAN", "THAT", "THEM", "THEN", "THEY", "THIS", "WHAT", "WHEN",
    "WILL", "WITH", "YOUR",
    # finance abbreviations / jargon
    "AI", "AH", "AM", "PM", "ET", "PT", "CT", "EOD", "EOW", "YTD", "YOY", "QOQ", "MOM",
    "ATH", "ATL", "DCA", "FUD", "IMO", "FYI", "WSB", "DD", "TA", "FA", "PE", "PEG",
    "EPS", "FCF", "ROE", "ROI", "P/E", "RSI", "MACD", "ADX", "EMA", "SMA", "VWAP",
    "CPI", "PPI", "GDP", "PCE", "FOMC", "FED", "ECB", "BOJ", "IRA", "IPO", "ETF",
    "CEO", "CFO", "COO", "CTO", "USD", "EUR", "GBP", "JPY", "USA", "UK", "EU", "OK",
    "Q1", "Q2", "Q3", "Q4", "FY", "H1", "H2", "TTM", "MRQ",
    "BUY", "SELL", "HOLD", "LONG", "CALL", "PUT", "BID", "ASK", "GAP", "RUN",
    "EV", "ESG", "AUM", "NAV", "OTC", "SEC", "IRS", "GAAP", "DIP", "PDT",
    # conversational filler — blocks a needless AI name->ticker lookup on
    # a message that names no company (still forced through by a cashtag).
    "ABOUT", "DOING", "GOING", "LOOKS", "LOOKING", "THINK", "THINKS", "TODAY",
    "TONIGHT", "MAYBE", "REALLY", "BETTER", "WORSE", "SHOULD", "WOULD", "COULD",
    "AGAIN", "STILL", "BEING", "GONNA", "WANNA", "PLEASE", "THANKS", "GUESS",
    "PRETTY", "QUITE", "THING", "STUFF", "OKAY", "SURE", "WELL", "KNOW", "WANT",
    "NEED", "MAKE", "TAKE", "LOOK", "FEEL", "SEEM", "SEEMS", "VERY", "MOVING",
}

# Cashtag / caret / explicit forms are high-confidence and skip shape
# gating entirely.
_RE_CASHTAG = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,5})(?:[.\-][A-Za-z]{1,2})?\b")
_RE_CARET = re.compile(r"(?<![A-Za-z0-9])\^([A-Z]{1,6})\b")
_RE_PAREN = re.compile(r"\(([A-Z]{1,5})\)")
_RE_TICKER_KW = re.compile(r"\bticker[s]?:?\s+([A-Z][A-Z.\-]{0,6})\b")
_RE_BARE = re.compile(r"(?<![A-Za-z0-9$^])([A-Z]{2,5})\b")

_RE_PRONOUN = re.compile(
    r"\b(it|its|it'?s|that|this|the stock|the name|the ticker|they|them|those)\b", re.I)
_RE_BARE_METRIC = re.compile(
    r"\b(p\s*/?\s*e|pe|peg|rsi|macd|adx|eps|ebitda|margin|margins|fcf|debt|guidance|"
    r"target|targets|valuation|multiple|support|resistance|trend|dividend|yield|"
    r"buyback|earnings|revenue|catalyst)\b", re.I)
_RE_PROPER_NOUN = re.compile(r"\b[A-Z][a-z]{2,}\b")
_RE_ALPHA_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z.'&-]{3,}\b")

# Common company / index names -> ticker. Deterministic, case-insensitive;
# checked before the AI name->ticker fallback. Values not already in the
# known set are still live-quote validated.
_NAME_TO_TICKER: dict[str, str] = {
    "alphabet": "GOOGL", "google": "GOOGL",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "meta platforms": "META", "meta": "META", "facebook": "META",
    "netflix": "NFLX",
    "broadcom": "AVGO",
    "palantir": "PLTR",
    "advanced micro devices": "AMD",
    "intel": "INTC",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "berkshire hathaway": "BRK.B", "berkshire": "BRK.B",
    "walmart": "WMT",
    "disney": "DIS",
    "the s&p": "SPY", "s&p 500": "SPY", "s&p500": "SPY", "sp500": "SPY", "spx": "SPY",
    "the nasdaq": "QQQ", "nasdaq 100": "QQQ", "ndx": "QQQ",
    "the dow": "DIA", "dow jones": "DIA",
    "russell 2000": "IWM",
}
_RE_NAME = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_NAME_TO_TICKER, key=len, reverse=True))
    + r")\b",
    re.I,
)

# Phrases that stand in for a group; matched (and masked) before the
# bare-token pass. -> [] means "no specific ticker" (the market baseline
# still answers). -> [TICKER] contributes that proxy.
_GROUP_PHRASES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\bmy (watchlist|names|positions|holdings|portfolio|book)\b", re.I), []),
    (re.compile(r"\bthe (market|tape|markets|indices|indexes)\b", re.I), []),
    (re.compile(r"\b(overall|broad market|risk[- ]?on|risk[- ]?off)\b", re.I), []),
    (re.compile(r"\bthe fed\b|\binterest rates\b|\brate cuts?\b", re.I), []),
    (re.compile(r"\b(semis|semiconductors?|chips?)\b", re.I), ["SOXX"]),
    (re.compile(r"\b(big tech|mega[- ]?cap tech|faang|magnificent 7|mag ?7)\b", re.I), ["XLK"]),
    (re.compile(r"\bsmall[- ]?caps?\b", re.I), ["IWM"]),
]


def _known_symbols() -> set[str]:
    global _known_cache, _known_built_at
    import time

    now = time.monotonic()
    if _known_cache and (now - _known_built_at) < _KNOWN_TTL:
        return _known_cache

    known: set[str] = set()
    try:
        from backend.api.main_helpers import _watched_symbols

        known.update(_watched_symbols())
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.regime.sector_engine import SECTOR_ETFS, SECTOR_MAP

        known.update(SECTOR_MAP.keys())
        known.update(SECTOR_ETFS.values())
    except Exception:  # noqa: BLE001
        pass
    try:
        known.update(settings.market_context.indices)
    except Exception:  # noqa: BLE001
        pass
    try:
        known.update(settings.relative_strength.benchmark_list())
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.scanner.scanner import market_scanner

        known.update(market_scanner.scan_results.keys())
    except Exception:  # noqa: BLE001
        pass

    known = {s.upper() for s in known if s}
    _known_cache, _known_built_at = known, now
    return known


def _cache_get(token: str) -> bool:
    if token in _VALID_CACHE:
        _VALID_CACHE.move_to_end(token)
        return True
    return False


def _cache_put(token: str) -> None:
    _VALID_CACHE[token] = True
    _VALID_CACHE.move_to_end(token)
    while len(_VALID_CACHE) > _VALID_CACHE_CAP:
        _VALID_CACHE.popitem(last=False)


def _validate_unknown(tokens: list[str]) -> set[str]:
    """Return the subset of ``tokens`` that resolve to a real live quote.

    Cache HITS only (a validated token) — never caches a miss, so a
    symbol that starts trading later isn't permanently rejected.
    """
    if not tokens:
        return set()
    good: set[str] = set()
    pending = []
    for t in tokens:
        if _cache_get(t):
            good.add(t)
        else:
            pending.append(t)
    if not pending:
        return good
    try:
        from backend.market_data.services.manager import market_data_manager

        quotes = market_data_manager.get_batch_quotes(pending)
    except Exception as e:  # noqa: BLE001
        logger.info("chat symbol validation failed for %s: %s", pending, e)
        return good
    for t in pending:
        q = quotes.get(t)
        if q is not None and str(getattr(q, "data_status", "")) != "ERROR" and (q.price or 0) > 0:
            good.add(t)
            _cache_put(t)
    return good


def _fuzzy1(a: str, b: str) -> bool:
    """True if ``a`` is one fat-finger from ``b`` — a single insert / delete /
    substitute, or one adjacent transposition. False for an exact match."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            i = diffs[0]
            return a[i] == b[i + 1] and a[i + 1] == b[i]
        return False
    short, lng = (a, b) if la < lb else (b, a)
    i = j = 0
    edited = False
    while i < len(short) and j < len(lng):
        if short[i] == lng[j]:
            i += 1
            j += 1
        elif edited:
            return False
        else:
            edited = True
            j += 1
    return True


def _nearest_known(token: str) -> str | None:
    """A single known ticker within one fat-finger of ``token``.

    Only for 4-5 char tokens that failed live-quote validation (a real
    ticker one edit away, like NVDL vs NVDA, validates first and never
    reaches here). Returns None unless exactly one known symbol matches,
    so an ambiguous typo (GOOG/GOOGL) falls back to asking.
    """
    if not (4 <= len(token) <= 5):
        return None
    cands = [
        k for k in _known_symbols()
        if 4 <= len(k) <= 5 and not k.startswith("^") and _fuzzy1(token, k)
    ]
    return cands[0] if len(cands) == 1 else None


def _mask(text: str) -> tuple[str, list[str]]:
    """Strip group phrases from ``text``; return (masked_text, proxies)."""
    proxies: list[str] = []
    for pattern, repl in _GROUP_PHRASES:
        if pattern.search(text):
            proxies.extend(repl)
            text = pattern.sub(" ", text)
    return text, proxies


def _looks_like_name(text: str) -> bool:
    """Worth one AI name->ticker lookup? True for a capitalized proper
    noun, or any 4+ char word left after group-phrase masking that isn't
    a stopword, a metric term, or conversational filler."""
    if _RE_PROPER_NOUN.search(text):
        return True
    masked, _ = _mask(text)
    for m in _RE_ALPHA_TOKEN.finditer(masked):
        w = m.group(0).strip(".'&-")
        if (
            w
            and w.upper() not in _CHAT_STOPWORDS
            and not _RE_BARE_METRIC.fullmatch(w.lower())
        ):
            return True
    return False


def extract_symbols(text: str) -> list[str]:
    """Tickers named in a single message (no carry-forward, no AI).

    Order preserved, de-duped. Runs the one batched live-quote lookup
    for unknown-but-plausible tokens.
    """
    if not text or not text.strip():
        return []

    masked, proxies = _mask(text)
    known = _known_symbols()
    ordered: list[str] = []
    seen: set[str] = set()

    def add(sym: str) -> None:
        sym = sym.upper()
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)

    for p in proxies:
        add(p)

    # High-confidence forms — collected across patterns and applied in
    # the order they appear in the message.
    hits: list[tuple[int, str]] = []
    for m in _RE_CASHTAG.finditer(masked):
        hits.append((m.start(), m.group(1).upper()))
    for m in _RE_CARET.finditer(masked):
        hits.append((m.start(), "^" + m.group(1).upper()))
    for m in _RE_PAREN.finditer(masked):
        hits.append((m.start(), m.group(1).upper()))
    for m in _RE_TICKER_KW.finditer(masked):
        hits.append((m.start(), m.group(1).rstrip(".-").upper()))
    for _, sym in sorted(hits):
        add(sym)

    # Company / index names -> ticker ("what about google" -> GOOGL).
    # Case-insensitive; targets not in the known set are live-quote
    # validated so a name never injects an untradeable symbol.
    name_targets = [
        (m.start(), _NAME_TO_TICKER[re.sub(r"\s+", " ", m.group(1).lower())])
        for m in _RE_NAME.finditer(masked)
    ]
    if name_targets:
        need = [tk for _, tk in name_targets if tk not in known]
        ok = _validate_unknown(need) if need else set()
        for _, tk in sorted(name_targets):
            if tk in known or tk in ok:
                add(tk)

    strong = set(seen)

    words = re.findall(r"[A-Za-z]+", masked)
    shouty = len(words) >= 4 and masked.upper() == masked

    # Case-insensitive mentions of a KNOWN ticker ("spy support?",
    # "how's aapl trending"). Safe even in a shouty all-caps sentence:
    # the known-set gate keeps ordinary words out and this never hits
    # live-quote validation. Stopwords ("IT", "ALL", ...) still excluded.
    for m in re.finditer(r"(?<![A-Za-z0-9$^.])([A-Za-z]{1,5})\b", masked):
        tok = m.group(1).upper()
        if tok not in strong and tok not in _CHAT_STOPWORDS and tok in known:
            add(tok)

    # Bare UPPERCASE tokens not in the known set -> one live-quote probe.
    # Skipped when the message is a shouty all-caps sentence (4+ words),
    # where case carries no signal. "RIVN?" / "SELL AAPL" are still read.
    if words and not shouty:
        weak_unknown: list[str] = []
        for m in _RE_BARE.finditer(masked):
            tok = m.group(1)
            if tok in strong or tok in _CHAT_STOPWORDS or tok in known:
                continue
            if len(tok) >= 2:
                weak_unknown.append(tok)
        validated = _validate_unknown(weak_unknown)
        for t in weak_unknown:  # keep original order
            if t in validated:
                add(t)
            else:  # fat-finger of a known ticker? ("AAPLE" -> "AAPL")
                near = _nearest_known(t)
                if near:
                    add(near)

    return ordered


# name->ticker AI results, keyed on the normalized message. Caches empty
# results too (a non-company message shouldn't re-hit the model) with a
# short TTL so a symbol that lists later isn't stuck.
_NAME_CACHE: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()
_NAME_CACHE_CAP = 128
_NAME_CACHE_TTL = 300.0


def _ai_resolve_name(text: str) -> list[str]:
    """One small completion: bare company name -> ticker(s). Validated.

    Result is cached (hits and misses) for ``_NAME_CACHE_TTL`` on the
    normalized message text so a repeated phrasing skips the AI call.
    """
    import time

    key = re.sub(r"\s+", " ", (text or "").strip().lower())[:200]
    now = time.monotonic()
    cached = _NAME_CACHE.get(key)
    if cached is not None and now - cached[0] < _NAME_CACHE_TTL:
        _NAME_CACHE.move_to_end(key)
        return list(cached[1])
    result = _ai_resolve_name_uncached(text)
    _NAME_CACHE[key] = (now, list(result))
    _NAME_CACHE.move_to_end(key)
    while len(_NAME_CACHE) > _NAME_CACHE_CAP:
        _NAME_CACHE.popitem(last=False)
    return result


def _ai_resolve_name_uncached(text: str) -> list[str]:
    try:
        from backend.ai.manager import ai_manager
        from backend.ai.prompt import extract_json_object
        from backend.ai.sync_bridge import run_sync

        # Chat name-resolution runs in sync/loop-less contexts (to_thread
        # worker threads) — bridge the async manager calls.
        if not run_sync(ai_manager.is_available()):
            return []
        resp = run_sync(ai_manager.complete(
            prompt=(
                "Extract US stock ticker symbols for any companies named in this "
                "message. Reply with a JSON object: {\"tickers\": [\"AAPL\", ...]}. "
                "Empty list if none.\n\nMessage: " + text
            ),
            system="You map company names to their US ticker symbols. JSON only.",
            max_tokens=120,
        ))
        if resp.text is None:
            return []
        import json

        data = json.loads(extract_json_object(resp.text))
        cand = [str(t).upper().strip() for t in (data.get("tickers") or []) if t]
    except Exception as e:  # noqa: BLE001
        logger.info("chat AI name->ticker resolution failed: %s", e)
        return []
    cand = [t for t in cand if 1 <= len(t) <= 6]
    validated = _validate_unknown([t for t in cand if t not in _known_symbols()])
    return [t for t in cand if t in _known_symbols() or t in validated]


def _carry_forward(transcript: list[tuple[str, str]]) -> list[str]:
    """Inherit tickers from the last 2 user turns (newest first)."""
    users = [content for role, content in reversed(transcript) if role == "user"]
    for content in users[:2]:
        got = extract_symbols(content)
        if got:
            return got
    return []


def resolve_turn_symbols(
    user_content: str,
    transcript: list[tuple[str, str]],
    base_symbols: list[str],
) -> tuple[list[str], bool]:
    """Resolve the tickers one chat turn should pull context for.

    Returns ``(symbols[:chat_max_tickers], capped)``. ``base_symbols``
    (a legacy symbol/alert session's own ticker) always comes first.
    """
    resolved: list[str] = []
    seen: set[str] = set()

    def add_all(items: list[str]) -> None:
        for s in items:
            s = s.upper()
            if s and s not in seen:
                seen.add(s)
                resolved.append(s)

    add_all(base_symbols)

    named = extract_symbols(user_content)
    if named:
        add_all(named)
    else:
        # Pronoun / bare-metric follow-up -> inherit the prior topic.
        if _RE_PRONOUN.search(user_content) or _RE_BARE_METRIC.search(user_content):
            add_all(_carry_forward(transcript))
        # Still nothing, but the message names something -> try the AI map.
        if not resolved and settings.ai.chat_symbol_ai_fallback and _looks_like_name(user_content):
            add_all(_ai_resolve_name(user_content))

    cap = settings.ai.chat_max_tickers
    return resolved[:cap], len(resolved) > cap
