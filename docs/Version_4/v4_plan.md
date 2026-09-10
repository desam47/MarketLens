# Version 4 — AI Integration

**Date:** 2026-09-09
**Last updated:** 2026-09-09 (Charts moved out to Version 5 — see `docs/Version_5/`)
**Status:** Active. Phase 4.1 done (4.1.1-4.1.16). Charts (was Phase 4.2) moved to Version 5 — v4's remaining scope is AI integration only.
**Scope:** Turn on and prove out the existing AI subsystem (providers, background jobs, prompt templates, `/api/ai/*` endpoints) end-to-end with a real provider, fixing whatever breaks. Charts was originally meant to open v4, then deferred to Phase 4.2 within it — now moved out entirely to open Version 5 instead (`docs/Version_5/v5_plan.md`); historical content kept below for the record.

---

## Goals

1. Enable AI analysis for real (`AI_ENABLED=true`) and prove the full pipeline works against an actual LLM, not just unit-test mocks.
2. Fix whatever breaks in the process — this is a "turn the key and see what's actually wired wrong" phase, not a feature-design phase. The AI subsystem (`backend/ai/*`, `/api/ai/*`) was built out across Versions 1-2 but nothing in the repo's history suggests it was ever run against a real provider.
3. Confirm the safety boundaries actually hold live (structured-output-only, AI never overwrites quant truth, uncertainty fallback on bad/missing data) — not just in mocked tests.

**Non-goals for this phase:**
- New AI-powered features (chat panel, scanner commentary, etc.) — that's a follow-on phase once the existing surface is proven solid, not part of "turn on what's built."
- Redesigning the provider-config schema for full per-provider settings (distinct API keys/models per fallback entry). The Phase 4.1 fix (below) makes the *existing* single-config-per-chain model correct for the common case (named providers fall back to their own sensible defaults); a fully general per-provider override system is a bigger, separate design question — revisit only if a real multi-paid-provider fallback need shows up.

---

## Phase 4.1 — Enable and Validate AI Integration End-to-End

**Why now:** `AI_ENABLED=false` was the safe default since Version 1. The subsystem underneath — provider abstraction (Ollama/OpenAI-compatible/Anthropic), `AIManager` fallback chain, `analyze_symbol()` structured pipeline, prompt templates, RQ background jobs — is fully built and unit-tested, but always against mocked HTTP, never against a live model. "Prove it out" means actually flipping it on and finding out what a real request does.

### What shipped

- ✅ 4.1.1 **Enabled AI for real.** `.env`: `AI_ENABLED=true`. Local Ollama confirmed already running with several models (llama3.2, qwen3, gpt-oss:20b, etc.) at `http://localhost:11434`. A second, real provider was also configured: an OpenAI-compatible gateway (`agentrouter.org`) serving a Claude Opus model, with a live API key.
- ✅ 4.1.2 **Fixed: unregistered provider name.** `AI_PROVIDER` was set to a descriptive label (`agentrouter`) that isn't one of the app's registered provider *types* (`ollama | lm_studio | openai | openrouter | anthropic | openai_compatible`) — `build_provider()` correctly rejected it as unknown, so the manager silently treated it as always-unhealthy. Fixed: `AI_PROVIDER=openai_compatible` (the existing generic type for "any other OpenAI-dialect endpoint") + `AI_BASE_URL` corrected to include the full `/v1` path, which `openai_compatible` requires explicitly (only `ollama`/`lm_studio` get `/v1` auto-appended). `.env.example`'s AI section was also out of date (comment only listed `ollama | openai | anthropic`, missing `lm_studio`/`openai_compatible`/`openrouter`) — corrected with guidance on when to use `openai_compatible` and the `/v1` requirement.
- ✅ 4.1.3 **Found and fixed a real bug: fallback providers inherited the primary's config.** `AIManager._get_provider()` applied `settings.base_url` / `settings.model` / `settings.api_key` to *every* provider in the chain, including fallbacks. A fallback is a different service by definition — with primary=the paid gateway and `AI_FALLBACK_PROVIDERS=ollama`, the "ollama" fallback was being built pointed at the *primary's* base_url asking for the *primary's* model name, so it silently inherited the exact same failure mode instead of actually falling back to the working local Ollama. Fixed in `backend/ai/manager.py`: only the primary provider (`name == settings.provider`) gets the configured base_url/model/api_key; every other provider in the chain now gets `None` for those three, which makes `build_provider()` fall through to that provider's own sensible defaults (e.g. ollama → `http://localhost:11434/v1` + `llama3.2`). Verified live: `GET /api/ai/status` went from `ollama: healthy=false` (silently misconfigured) to `ollama: healthy=true` (its own endpoint, correctly reachable) after the fix, with zero new config needed.
- ✅ 4.1.4 **Ran a real end-to-end analysis.** `POST /api/ai/analyze?symbol=SPY&timeframe=1d` against the live backend, live database, and the real agentrouter/Claude-Opus provider — got back a well-formed, schema-valid `AnalysisResponse` (`trend: "mixed"`, sensible summary, supporting/risk factors, a flagged timeframe conflict) in ~4s. Confirms the full chain works live: `build_context()` → real quant data → `ai_manager.complete()` → real HTTP call → `parse_ai_reply()` → Pydantic validation → response.
- ✅ 4.1.5 **Fixed: the frontend "Re-run" button 405'd every time.** `AIAnalysisPanel`'s `api.analyzeSymbol()` called the shared `fetch()` helper with no `method` override, defaulting to GET; `POST /api/ai/analyze` is POST-only. Existed since the endpoint was built — never surfaced because AI was off until this phase, so nobody had clicked it live against a running, enabled backend before. Fixed in `frontend/src/services/api.ts` (added `{ method: 'POST' }`); verified live (GET→405, POST→200 on the same URL) and swept the rest of `api.ts` for the same bug class — no other instances found.
- ✅ 4.1.6 **Enabling AI for real exposed three test-isolation gaps in the unrelated NL-search feature.** `backend/nl_search/` (query parsing) and `backend/api/nl_search/router.py` (result explanation) each import their own `ai_manager` reference and were only ever exercised with AI truly off — three tests broke the instant AI went live, all for the same reason: mocking one module's `ai_manager` import doesn't mock a *different* module's separate import of the same singleton.
  - `test_parser.py::test_garbage_falls_to_default` didn't mock AI at all — with a real, reachable Ollama fallback, the nonsense query `"asdfghjkl"` got a confident-but-wrong AI-sourced parse (`used="ai"`) instead of correctly falling through to `"default"`. Not an AI-quality bug to fix — the test's actual point is the rules-miss → AI-miss → default fallback chain, independent of what a live backend happens to do with gibberish. Fixed by mocking `ai_manager.is_available()` False for this test.
  - `test_nl_search_router.py::test_rule_based_query_returns_200` / `test_explain_true_includes_explanation` both mocked `backend.nl_search.parser.ai_manager` (controls query *translation*) but not `backend.api.nl_search.router.ai_manager` (controls result *explanation*, which defaults to `explain=True` on every request) — so with AI genuinely available, `_maybe_explain` ran for real and generated a real explanation neither test expected. Fixed by adding the second mock, mirroring the pattern the file's own `test_explanation_when_ai_on_and_explain_true` already used correctly.
- ✅ 4.1.7 **Live failure found via the frontend, not a curl test: the local Ollama fallback's replies kept getting rejected.** Clicking "Re-run" surfaced `AI response could not be parsed: ... Input should be 'bullish', 'bearish', 'neutral', 'mixed' or 'uncertain' [type=literal_error, input_value='downtrend', ...]. Provider: ollama. Model: llama3.2.` — the primary was briefly unavailable (see the flakiness note below), the request fell to Ollama, and llama3.2 followed the system prompt's rule to agree with the engine's `trend_state.direction` a little too literally: it echoed the ENGINE's own vocabulary (`TrendDirection`/`TrendClassification` use `uptrend`/`downtrend`/`sideways`/`strong_bullish`/`no_signal`/etc., all visible in the context it's given) instead of translating to the four-word output vocabulary the prompt actually asks for. A predictable near-miss, not gibberish — worth normalizing rather than discarding. Added a `field_validator("trend", mode="before")` on `AnalysisResponse` (`backend/ai/prompt.py`) mapping known engine-vocabulary synonyms (uptrend→bullish, downtrend→bearish, sideways/flat/range/choppy→neutral, strong_bullish/weak_bearish/etc.→bullish/bearish, no_signal/unknown→uncertain) before the strict `Literal` check runs; anything not in the map still hits that check unchanged, so true gibberish still correctly fails to an `UncertaintyResponse`. Also sharpened `SYSTEM_PROMPT` rule 2 with an explicit "translate, don't copy" example to reduce how often this happens at the source (a prompt tweak alone wouldn't have been reliable enough on its own — small local models don't always follow it — hence the code-level normalization is the load-bearing fix).
- ✅ 4.1.8 **Real bug found live, third time: a bad response body crashed the whole request with a raw 500 instead of falling through.** After the user edited `.env` to try a different model/base_url (dropping the `/v1` path `openai_compatible` requires — flagged to the user, not silently fixed, since it's their config to decide on), `POST /api/ai/analyze` returned a bare `{"detail": "Internal server error"}`, not the graceful `UncertaintyResponse` the module's own design promises for every "AI didn't give us a good answer" case. Traced to `OpenAICompatibleProvider.complete()` (`backend/ai/providers.py`): the gateway returned HTTP 200 with an HTML body (its own web UI, landed on because the missing `/v1` path resolved to the wrong route) — `r.json()` raised a raw `json.JSONDecodeError` that nothing in the call chain caught, unlike every other bad-response shape (4xx/5xx/malformed-JSON-payload), which are deliberately caught and re-raised as `ProviderUnavailable` so the manager can fall through or return uncertainty. Fixed by wrapping `r.json()` in the same pattern; found and fixed the identical gap in `AnthropicProvider.complete()` while in the file. After the fix, the same request correctly returns `UncertaintyResponse` (`"AI providers unavailable (tried: none)"`) instead of a 500 — the underlying `/v1` config mistake is still there (that's the user's to fix), but it no longer crashes the endpoint.
- 6 new regression tests (`backend/tests/ai/test_ai_manager.py` — `TestFallbackProviderIsolation`), 2 pre-existing tests fixed for environment-coupling now that `AI_ENABLED=true` is the real, intentional `.env` state (they were asserting the live `.env`'s values rather than `AISettings`' own field defaults via `_env_file=None`), 3 more pre-existing tests fixed per 4.1.6, 1 new subtest-parametrized regression test per 4.1.7 (`test_phase16_analyze.py::test_normalizes_engine_vocabulary_synonyms`, 11 cases), 2 new regression tests per 4.1.8 (one per provider class). Full suite: see run at commit time.

### Known, not fixed (flagged, not blocking)

- **Health-check flakiness on the agentrouter gateway.** `/api/ai/status` sometimes reports the primary as `healthy: false` (its health check hits `GET /v1/models`) even though the actual `POST /v1/chat/completions` call that matters succeeds — confirmed both ways within the same few seconds during 4.1.4's live test. Root cause not confirmed (rate-limiting on the models-list endpoint specifically? something else on the gateway's side?) — not chased further since the fallback chain (now correctly fixed in 4.1.3) means an occasional false-negative health check just costs one extra hop to Ollama, not a broken request. Revisit if it causes visibly wrong behavior (e.g. `/api/ai/status` UI badge flickering) rather than just an internal fallback hop.

### Verification
- `GET /api/ai/status` — primary provider name matches the configured type (not an unregistered label); each provider's `healthy` reflects *its own* endpoint, not a copy of the primary's.
- `POST /api/ai/analyze?symbol=SPY` — returns a schema-valid response with a real provider/model name, not `"disabled"`/`"none"`.
- `backend/tests/ai/test_ai_manager.py` — `TestFallbackProviderIsolation` passes; confirms a fallback never receives the primary's `api_key`.

### Addendum — Items 4.1.9-4.1.16 (hardening pass from continued live use)

4.1.1-4.1.8 proved the pipeline works end-to-end against a real provider.
That was the first successful request, not the last bug — continuing to
actually *use* the now-live features (real AI Stock Search queries, real
AI Analysis re-runs, an `.env` audit prompted by a question about what
the AI_* settings do) kept surfacing independent, real issues. Same
"turn the key and see what's actually wired wrong" mode as the rest of
4.1, just not stopping at the first green run.

- ✅ 4.1.9 **AI Stock Search's "Market" scope dropdown was silently ignored — fixed twice, the second fix being "there's nothing real to fix."** First pass (`commit c22443d`): the Watchlist/Market dropdown's value reached `NLFilters.scope` only via the rule-based parse path's `setdefault`; the AI-parse path and the graceful-default path both silently reverted to the schema default (`"watchlist"`) regardless of the dropdown. Fixed in `parse_query()` by applying `base["scope"]` unconditionally as a final step, not threaded through each path separately — can't be dropped again by a future path added there. User re-tested and still saw watchlist-only results with "Market" selected. Second investigation (`commit af40295`) found the real problem: **there is no market-wide scanning capability anywhere in the app.** `execute_query()`'s "market" branch reads whatever's incidentally cached from watchlist scans — by original design, never wired to a real universe. Presented three real tradeoffs (build market-wide scanning as a new capability, relabel the dropdown to be honest about what it does, or remove it); user chose removal. `NLSearchBar.tsx`'s scope `<select>` deleted entirely; the feature always searches watchlist scope now — the one path that's actually real. (The backend scope-override mechanism from the first fix is harmless and correctly tested, just unreachable from the UI now.)
- ✅ 4.1.10 **Optimization pass found a real concurrency bug, not just slow requests.** User asked to optimize AI Stock Search (`commit 5d0c593`). `POST /api/nl-search` is `async def`, but both its AI calls (query translation, result explanation) went through synchronous `httpx.Client` called directly rather than via `asyncio.to_thread` — every AI-backed search blocked the *entire* FastAPI event loop (WebSocket price broadcasts, every other HTTP request in flight) for the full external round-trip, not just its own request. Proved live before/after: a concurrent `/api/system/status` call (baseline ~3ms) took ~2s while an AI call was in flight pre-fix; ~5-10ms during a 6.7s AI call post-fix. Wrapped both calls in `asyncio.to_thread`, the same pattern `backend/api/ai/router.py` already used for the identical class of call. Also fixed two latency issues on top: bare "bullish stocks"/"bearish stocks" (no timeframe/ranking keyword) matched no existing rule and paid a full AI round-trip for something trivial — added a last-resort direction-only rule, placed after every more specific rule via `setdefault` so it's a true fallback (verified: `parser_used` went `ai` → `rules` for that exact query). And added a bounded LRU cache for successful AI translations — the query text alone determines the translation, independent of live market data, so a repeated query (or re-clicked example pill) now skips the AI round-trip entirely on a cache hit. Only successes are cached; failures aren't, so a transient provider hiccup can't wall off a query string from ever retrying.
- ✅ 4.1.11 **User report ("AI Stock Search sometimes says no symbols matched") traced to a trend-engine calibration bug affecting the whole app, not a search-specific issue.** (`commit 041ec0b`) `backend/trend/trend_engine.py` computed `confidence = abs(avg_signal)` — the exact same weighted signal value used, at a threshold of just 0.15-0.35, to decide direction in the first place. So the instant a trend was confirmed, confidence sat at 0.15-0.35 by construction — permanently below every `min_confidence >= 0.5` default used throughout the app (NL search's schema default, `DailyBullish`/`DailyBearish`, `MinTimeframeBullish`/`Bearish`, `MTFAlignment`, the scanner's own `MULTI_TIMEFRAME_BULLISH`/`BEARISH` signal at `confidence > 0.6`). Verified live: watchlist symbol DVLT had a genuine 1d downtrend (confidence 0.365) that "bearish stocks" still couldn't surface. Fixed by rescaling confidence so crossing the direction threshold maps to 0.5 (not the threshold's own raw value) and the maximum signal maps to 1.0, continuous through the sideways boundary; the existing STRONG/WEAK strength multipliers apply unchanged on top. Confirmed isolated to `confidence` only — `TrendSignal.score`/`classification` come from a separately-computed `raw_score`, untouched (checked every test file that constructs a `TrendSignal` from real computation, not just a fixture). Verified live post-restart: real downtrending watchlist symbols read confidence 0.63-0.69 (was 0.2-0.6), "bearish stocks" now correctly finds them. Side effect noted: this also makes the scanner's `MULTI_TIMEFRAME_BULLISH`/`BEARISH` signals achievable in realistic conditions for the first time — they were effectively dead under the old formula.
- ✅ 4.1.12 **AI Stock Search's trend/signal columns were the one place in the app not using the existing color convention.** (`commits ef452ff`, `f407baf`) Every other trend-direction display (`TrendCard`, `MTFScoreGrid`, `ConfluenceCard`, `ScannerPage`'s `.cell-trend` badges) already color-codes uptrend=green/downtrend=red/sideways=yellow; `NLSearchBar.tsx`'s `trendLabel()` rendered plain uncolored glyphs. Matched the app's exact existing hex values (`#22c55e`/`#ef4444`/`#eab308`) rather than inventing new ones. Extended the same treatment to signal chips (RSI oversold/overbought, MACD bullish/bearish, MTF bullish/bearish, High Vol), reusing the bullish/bearish signal classification `TopMoversCard.tsx` already applies to these exact names; `HIGH_VOLUME` (direction-agnostic — says something moved, not which way) gets the neutral/yellow treatment.
- ✅ 4.1.13 **Real bug found live via the frontend, a fourth time: AI Analysis's "Re-run" showed "Failed to fetch."** (`commit 0cd8653`) Root cause: the configured `openrouter/free` model rate-limited the request (HTTP 429). Both provider classes routed any 4xx that wasn't 401/403/404/5xx into a deliberate `r.raise_for_status()` — "the request is broken, the caller should see the raw error" — a reasonable rule for a genuinely malformed request, but nothing upstream (`AIManager.complete` → `analyze_symbol` → the FastAPI endpoint) ever caught the resulting `httpx.HTTPStatusError`. It reached FastAPI as an unhandled exception, which the browser reported as a network failure rather than resolving a normal (if unhappy) HTTP response — despite `/api/ai/analyze`'s own docstring promising it never 500s for a provider-side failure. 429 was miscategorized: it means the provider is temporarily saying no, not that the request itself is broken — moved it into the same recoverable bucket as 401/403/404/5xx (both `OpenAICompatibleProvider` and `AnthropicProvider`). Deliberately left plain 400 alone — an existing test (`test_complete_raises_for_status_on_400`) documents that a genuinely malformed request should still fail loud, a prior intentional design choice this bug didn't touch. Verified live: the same AAPL analyze call that 500'd now returns 200 with the app's existing "AI providers unavailable" graceful degradation, CORS headers intact (checked with an `Origin` header — curl alone doesn't exercise that path).
- ✅ 4.1.14 **A direct question about what the `AI_*` `.env` settings do surfaced a real misconfiguration.** `AI_MAX_TOKENS=100000` — 100x the code's own default of 1000 (`backend/config/settings.py`) — and, unlike the NL search calls (which pass their own small explicit `max_tokens`), both AI Analysis call sites (`AIAnalysisPanel.tsx`'s Re-run, `AITemplatesPanel.tsx`) rely entirely on this setting, since neither passes an override. So every analyze call was requesting up to 100,000 output tokens for a short structured JSON summary — a plausible contributor to the 429s in 4.1.13, since many providers rate-limit by token budget, not just request count. Corrected the live `.env` to `2000` (`.env` is gitignored — no commit; `.env.example` already documented the correct `1000` default, so it needed no change, only the live file had drifted from it). `AI_TIMEOUT=30.0`, `AI_HEALTH_CHECK_TIMEOUT=2.0`, and `AI_TEMPERATURE=0.3` were audited in the same pass and confirmed already correct — the first two just restate the code's own defaults, and 0.3 is genuinely well-suited to a consistent, non-creative analytical task.
- ✅ 4.1.15 **Stopped hardcoding temperature in the two NL search AI calls.** (`commit 920a5bb`) Query translation hardcoded `temperature=0.0`, result explanation hardcoded `0.3` — both silently ignoring the configured `AI_TEMPERATURE`. Per explicit instruction, removed both per-call overrides so they behave like every other AI call in the app. Also dropped the framing of translation as a special "fixed-vocabulary, must be deterministic" task — it's a real AI-driven translation call and should be treated as one. This weakens (but doesn't eliminate) the 4.1.10 translation cache's original justification, which relied on temperature=0.0 for a literal purity guarantee — updated that comment to be honest about the tradeoff: a little per-call sampling diversity traded for consistency + speed on repeated queries, which still holds at a non-zero configured temperature.
- ✅ 4.1.16 **Deleted `backend/nl_search/router.py`, an unreferenced dead-code duplicate of the live `backend/api/nl_search/router.py`.** (`commit c67126e`) `main.py` only ever mounted the `api/nl_search` copy; `backend/nl_search/__init__.py`'s public surface never referenced the other one either — confirmed via grep, nothing in the app or test suite imported it. Not merely inert: it nearly got edited in place of the real router during 4.1.15 (patched "for consistency" before this cleanup caught the duplication), exactly the failure mode an unreferenced copy invites.

**Tests (4.1.9-4.1.16):** 8 new for the bare-direction rule + translation cache behavior (hit/miss/case-insensitivity/no-cross-contamination/failure-not-cached); 4 new for the confidence rescale (confirmed uptrend/downtrend clears 0.5, flat data stays below it, bounds stay in [0,1]); 2 new for the 429 fix (one per provider class). Each fix's test run was scoped to the files it touched (not the full suite), per standing instruction to ask before running the full suite rather than default to it.

---

## Phase 4.2 — Charts — MOVED TO VERSION 5 (2026-09-09)

**This phase no longer lives in Version 4.** Originally meant to open
Version 4 (carried forward from Version 3's never-started Phase 3.4);
bumped to make room for Phase 4.1, then — once 4.1 was done and
hardened through several rounds of live-use fixes (see
`phase_audit_v4.md` items 4.1.9-4.1.16) — moved out of v4 entirely to
open Version 5 as its own phase (5.1) instead of staying a secondary
item here. Canonical, going-forward copy: `docs/Version_5/v5_plan.md`.
The content below is left in place as the historical as-planned
record for when this scope still lived in v4 — nothing in it has
changed, only where it's tracked going forward.

**Why now (whenever this phase actually starts):** Charts are the most-visited surface. Drawing tools require manual timestamp entry (broken UX). Only one chart type (candlestick). Limited indicator set.

**Drawing decisions confirmed (from the original v3 scoping — re-confirm still current before building):**
- Types v1: **Trend line** (2-point) + **Horizontal line** (1-point, price-level) — scope kept to these two
- Activation: **Floating toolbar** inside the chart card (top-left strip of buttons)
- Interaction: **Click → click** to place. **Esc** cancels mid-draw. One click for horizontal line, two for trend line.
- Storage: **DB-backed** (existing CRUD API, unchanged) — drawing saves to server immediately after second click
- Lock toggle: small icon button in the toolbar; when locked, chart ignores drawing clicks

### Items

#### 4.2.1 Drawing tools: click-to-place (trend line + horizontal line)
- **Current state:** `DrawingToolsPanel.tsx` form requires typing start/end timestamps and prices. Clunky.
- **New behavior (v1):**
  1. Floating toolbar inside `CandlestickChart` card (top-left): `[T↗] [—] 🔓`
  2. Click a tool button → enter "draw mode" for that tool (button highlights active)
  3. Click on the chart → place point 1 (shows a marker dot)
  4. Click again → place point 2, drawing commits → POST to API → added to the sidebar list
  5. Press Esc → cancel draw mode, remove marker, no API call
  6. Drawing lock (🔓→🔒): when locked, clicks are ignored (prevent accidental edits)
- **Implementation:**
  - New file: `frontend/src/components/chartInteractions.ts` — pure `pixelToCoord(chart, x, y) -> {time: number, price: number}` using `chart.timeScale().coordinateToTime()` and `chart.priceScale().coordinateToPrice()`
  - Add `onChartClick(time, price)` callback prop to `CandlestickChart`; when a drawing tool is active and chart is unlocked, emit this
  - `CandlestickChart` state machine: `idle | placing-start | placing-end | drawing-locked`
  - On `placing-start` click: show a temporary marker div; transition to `placing-end`
  - On `placing-end` click: call `api.createDrawingTool(...)`, reset to `idle`
  - On Esc keydown: if in `placing-*`, remove marker, transition to `idle`
  - After save: refresh `DrawingToolsPanel` list (existing `fetchDrawings` refetch)
- Drawing types supported in v1: `trend_line` (needs 2nd click), `horizontal_line` (needs 1 click — price level only, no time)

#### 4.2.2 Chart toolbar: drawing lock toggle + tool activation
- Add lock state to `CandlestickChart`: `const [drawLock, setDrawLock] = useState(false)`
- Toolbar buttons: `Trend Line`, `Horizontal`, lock icon
- Active tool indicator (one at a time, cleared on Esc or on drawing save)
- Keyboard handler: listen for `Escape` key globally when in draw mode

#### 4.2.3 Additional chart types
- **Line chart:** `frontend/src/components/LineChart.tsx` — close-only line, no candles, no wicks. Useful for long timeframes.
- **Area chart:** `LineChart.tsx` with a filled area under the line.
- **Heikin Ashi:** variant of `CandlestickChart.tsx` that takes `chartType="heikin_ashi"` and pre-computes HA candles from the raw data.
- **Renko / Kagi / P&F:** out of scope (see Non-goals) — only if all other items land first and there's demand.
- Add `chart_type` to the `SymbolPage` toolbar with a dropdown

#### 4.2.4 More indicators
- Currently supported: SMA, EMA, MACD, Bollinger (per `CustomIndicatorsPanel.tsx`).
- Add: RSI, VWAP, Ichimoku Cloud, ATR, Stochastic, ADX, OBV, Williams %R, CCI
- File: `frontend/src/components/CustomIndicatorsPanel.tsx` — add the new entries to the dropdown
- For built-in indicators, add a "Built-in" section in the panel that doesn't require the user to configure anything (just toggle on/off)
- For VWAP: needs both price and volume; the bars data already has `volume` populated (per `market_data_sql.py`)

#### 4.2.5 Indicator overlay vs separate pane
- **Overlay (price chart):** SMA, EMA, Bollinger, VWAP, Ichimoku
- **Separate pane below price:** RSI, MACD, Stochastic, ADX, ATR, Williams %R, CCI, OBV
- Add a `pane_height` config to custom indicators; UI shows a small thumbnail of where it renders

#### 4.2.6 Chart settings + drawing persistence
- Chart settings (timeframe, indicator set, chart type): persist per-user via `localStorage` keyed by symbol
- Drawings: already persisted to DB via existing `DrawingTool` API
- Verify the click-to-place flow doesn't break the persistence model — the API should still receive `start_timestamp` / `start_price` from the click handler
- Drawing lock (introduced in 4.2.2) prevents accidental edits

### Before starting — re-verify against current code
- `DrawingToolsPanel.tsx`, `CustomIndicatorsPanel.tsx`, `CandlestickChart.tsx` still exist with roughly this shape
- The `DrawingTool` CRUD API (`backend/api/...`) is unchanged
- `lightweight-charts` is still the pinned charting library (`frontend/package.json`)
- A TradingView Charting Library integration was attempted once (commit `9598dae`) but isn't present in the current tree — don't reach for it without a specific reason; `lightweight-charts` is the live implementation this plan builds on.

### Verification
- Click-to-place: open chart, click two points, see trendline appear at correct (timestamp, price)
- Switch to line chart via toolbar dropdown — renders without errors, no wicks
- Toggle RSI on — separate pane appears below price chart
- All chart tests pass; add a new `frontend/src/components/__tests__/chartInteractions.test.ts`

---

## Open questions

1. Now that 4.1 is done and hardened, what's the first real AI-powered *feature* to build on top (chat panel? scanner commentary? automated daily summary?) — separate decision, not resolved yet.

Charts-specific open questions (TradingView re-attempt, Renko/Kagi/P&F demand) moved to `docs/Version_5/v5_plan.md` along with the rest of that scope.
