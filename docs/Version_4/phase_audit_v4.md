# Version 4 Phase Audit

**Last updated:** 2026-09-09 (Phase 4.1: AI integration enabled and validated end-to-end)
**Status:** Active.
**Scope:** AI integration (enable + validate the existing subsystem end-to-end), then Charts (deferred within this version, not dropped).

---

## Scorecard

| # | Phase | Status | Notes |
|---|---|---|---|
| 4.1 | Enable and Validate AI Integration End-to-End | ✅ DONE | Enabled `AI_ENABLED=true`; fixed an unregistered-provider-name config error and a real fallback-provider config-isolation bug; ran a live `/api/ai/analyze` call against a real provider. Full detail in `docs/Version_4/v4_plan.md`. |
| 4.2 | Charts (drawing v1, line/area/HA, indicators) | ⬜ NOT STARTED | Deferred within v4 to make room for 4.1 — not dropped. Full scope in `docs/Version_4/v4_plan.md`. |

---

## Phase 4.1 — Enable and Validate AI Integration End-to-End — ✅ DONE (2026-09-09)

**Trigger:** User request — focus v4 on AI integration instead of Charts; "turn on and prove out what's already built" (chosen over building a new AI feature or expanding provider support, at a decision point where the AI subsystem turned out to be fully built but switched off with no evidence it had ever run against a real provider).

**Items:**
- ✅ 4.1.1 `AI_ENABLED=true` in `.env`. Local Ollama confirmed already running (several models available). A second real provider — an OpenAI-compatible gateway serving a Claude Opus model, with a live API key — was also configured for this test.
- ✅ 4.1.2 Fixed unregistered provider name: `AI_PROVIDER` was set to a descriptive label not in the registered set (`ollama | lm_studio | openai | openrouter | anthropic | openai_compatible`); `build_provider()` correctly rejected it, silently marking the provider always-unhealthy. Fixed to `AI_PROVIDER=openai_compatible` (the existing generic type for custom OpenAI-dialect endpoints) with `AI_BASE_URL` corrected to include the required `/v1` path. `.env.example`'s AI section comment was also stale (missing `lm_studio`/`openai_compatible`/`openrouter`) — corrected with guidance on `openai_compatible` + the `/v1` requirement.
- ✅ 4.1.3 **Real bug found and fixed:** `AIManager._get_provider()` (`backend/ai/manager.py`) applied the primary's `base_url`/`model`/`api_key` to *every* provider in the fallback chain — so a fallback (a different service by definition) silently inherited the primary's config instead of using its own. With primary=the paid gateway and fallback=`ollama`, the "ollama" fallback was being pointed at the *primary's* base_url asking for the *primary's* model — carrying the exact same failure as the primary instead of actually falling back. Fixed: only the primary (`name == settings.provider`) gets the configured base_url/model/api_key; every other chain entry gets `None`, letting `build_provider()`'s own per-provider defaults apply. Verified live: `GET /api/ai/status` for `ollama` went from `healthy=false` (silently misconfigured) to `healthy=true` after the fix, with zero new `.env` entries needed.
- ✅ 4.1.4 Ran a real end-to-end analysis: `POST /api/ai/analyze?symbol=SPY&timeframe=1d` against the live backend/database and the real gateway provider — returned a schema-valid `AnalysisResponse` (trend, confidence, supporting/risk factors, a flagged timeframe conflict) in ~4s. Confirms the full pipeline (`build_context` → `ai_manager.complete` → real HTTP call → `parse_ai_reply` → Pydantic validation) works live, not just under mocks.
- ✅ 4.1.5 **Real bug found live via the frontend:** `AIAnalysisPanel`'s "Re-run" 405'd every time. `api.analyzeSymbol()` (`frontend/src/services/api.ts`) called the shared fetch helper with no `method`, defaulting to GET; `POST /api/ai/analyze` is POST-only. Existed since the endpoint was built — never surfaced with AI off. Fixed by adding `{ method: 'POST' }`; verified live (GET→405, POST→200) and swept the rest of `api.ts` for the same bug class (no other instances found).
- ✅ 4.1.6 Enabling AI for real broke 3 pre-existing tests in the unrelated NL-search feature (`backend/nl_search/`, `backend/api/nl_search/router.py`), each importing its own `ai_manager` reference independently — mocking one didn't mock the other. `test_parser.py::test_garbage_falls_to_default` didn't mock AI at all and got a confident-but-wrong AI parse of nonsense input instead of the expected rules→AI→default fallthrough; `test_nl_search_router.py::test_rule_based_query_returns_200` / `test_explain_true_includes_explanation` mocked the parser's `ai_manager` (query translation) but not the router's separate one (result explanation, `explain=True` by default) — so a real explanation got generated where both tests expected none. All three fixed by mocking the actual reference each code path uses.
- ✅ 4.1.7 **Real bug found live via the frontend, second time:** clicking "Re-run" again surfaced `AI response could not be parsed: ... trend Input should be 'bullish', 'bearish', 'neutral', 'mixed' or 'uncertain' ... input_value='downtrend' ... Provider: ollama. Model: llama3.2.` The primary was briefly unavailable (see the flakiness note below), the request fell to Ollama, and llama3.2 echoed the ENGINE's own trend vocabulary (`TrendDirection`/`TrendClassification`: `uptrend`/`downtrend`/`sideways`/`strong_bullish`/`no_signal`/etc. — all visible in the context it's shown) instead of translating to the 5-word output vocabulary the prompt actually specifies. A predictable near-miss, not gibberish. Added a `field_validator("trend", mode="before")` on `AnalysisResponse` (`backend/ai/prompt.py`) normalizing known engine-vocabulary synonyms before the strict `Literal` check runs; anything not in the map still fails validation unchanged (confirmed: the pre-existing `test_rejects_trend_outside_vocabulary` still passes). Also sharpened `SYSTEM_PROMPT` rule 2 with an explicit translate-don't-copy example — a prompt tweak alone isn't reliable enough on its own with smaller local models, so the code-level normalization is the load-bearing fix.

**Known, not fixed:** the primary gateway's health-check endpoint (`GET /v1/models`) is flakier than its actual completion endpoint (`POST /v1/chat/completions`) — `/api/ai/status` sometimes shows the primary as unhealthy even when a real analysis call through it succeeds moments later. Not chased further: the 4.1.3 fix means a false-negative health check just costs one extra hop to a correctly-configured Ollama fallback, not a broken request — which is exactly the path that surfaced 4.1.7.

**Tests:** 6 new (`backend/tests/ai/test_ai_manager.py::TestFallbackProviderIsolation` — asserts a fallback's `base_url`/`model` are its own defaults and it never receives the primary's `api_key`); 2 pre-existing tests fixed for now-real environment coupling (`AI_ENABLED=true` is genuine `.env` state as of this phase, not a test fixture default — fixed via `_env_file=None` to test `AISettings`' actual field defaults in isolation, matching the existing house pattern from `test_aux_data_settings.py`); 3 more pre-existing tests fixed per 4.1.6; 1 new subtest-parametrized test per 4.1.7 (`test_phase16_analyze.py::test_normalizes_engine_vocabulary_synonyms`, 11 cases).

**Verification:**
```bash
curl -s http://127.0.0.1:5001/api/ai/status | python3 -m json.tool
curl -s -X POST "http://127.0.0.1:5001/api/ai/analyze?symbol=SPY&timeframe=1d" | python3 -m json.tool
```
