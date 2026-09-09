# Version 4 — AI Integration

**Date:** 2026-09-09
**Last updated:** 2026-09-09
**Status:** Active. Phase 4.1 in progress.
**Scope:** Turn on and prove out the existing AI subsystem (providers, background jobs, prompt templates, `/api/ai/*` endpoints) end-to-end with a real provider, fixing whatever breaks. Charts (originally meant to open v4) is deferred within this version — see Phase 4.2 — not dropped.

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

---

## Phase 4.2 — Charts (deferred within v4, not dropped)

Originally meant to open Version 4 (carried forward from Version 3's never-started Phase 3.4); bumped to make room for Phase 4.1. Full scope preserved below exactly as planned — nothing here has changed, only its position in the roadmap.

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

1. Once 4.1 is solid, what's the first real AI-powered *feature* to build on top (chat panel? scanner commentary? automated daily summary?) — separate decision, not part of this phase.
2. Charts (4.2): re-attempt TradingView at some point, now that lightweight-charts has more mileage on it? Revisit only if lightweight-charts hits a real ceiling — no evidence of that yet.
3. Charts (4.2): Renko/Kagi/P&F — gauge demand after v1 ships before scoping the data-transformation work.
