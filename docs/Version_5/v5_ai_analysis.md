# Version 5 AI Analysis Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (review: 15 findings logged, converted to the bug-fix tracker format)
**Status:** Open. The 2026-09-24 review found 15 issues; none is fixed yet. The core implementation (last code commit `43e00bf`) is otherwise complete; see [Reference](#reference).
**Scorecard:** 0 ✅ COMPLETE, 0 ⚠️ PARTIAL, 15 ❌ NOT STARTED, 0 🟡 DEFERRED.
**Source:** 2026-09-24 review of `backend/ai/analyze.py`, `backend/ai/context.py`, `backend/ai/prompt.py` (analysis and trade-plan models), `backend/ai/tasks.py`, `backend/ai/trade_plan_tracker.py`, `backend/api/ai/router.py`, `backend/api/ai/jobs.py`, and `frontend/src/components/AIAnalysisPanel.tsx`, at `b7db6c8`.
**Related:** [Phase audit](phase_audit_v5.md), [Chat bug fixes](v5_bug_fixes.md), [Phase 5.8 evaluation](phase_5_8_evaluation.md)

"Verified" means the behaviour was reproduced with a throwaway probe test
run under the project's offline pytest guards, or shown by a read-only
query of the live database. "Code-read" means it follows from the code but
was not reproduced.

Status legend (same as the phase audits):

- ✅ **COMPLETE** — fixed and covered by tests
- ⚠️ **PARTIAL** — the harmful behaviour is fixed, but specific gaps remain (listed in the entry)
- ❌ **NOT STARTED** — no change made yet
- 🟡 **DEFERRED** — intentionally postponed

Line numbers refer to the code at `b7db6c8`.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| AA-01 | High | Backend | Analysis blocks the server's event loop while it builds context | Verified | ❌ NOT STARTED |
| AA-02 | Medium | Backend | One inconsistent plan level discards the whole analysis | Verified | ❌ NOT STARTED |
| AA-03 | Medium | Backend + UI | Background (template) results drop evidence, validation and context | Verified | ❌ NOT STARTED |
| AA-04 | Medium | Tracking | Grading never sees the day a plan was tracked | Verified | ❌ NOT STARTED |
| AA-05 | Medium | Tracking | Tracked plans aren't tied to an analysis and lose model and timeframe | Code-read | ❌ NOT STARTED |
| AA-06 | Medium | Validation | Plan validation ignores the quote's age | Verified | ❌ NOT STARTED |
| AA-07 | Low | Backend | Transient failures are cached for 45 seconds | Verified | ❌ NOT STARTED |
| AA-08 | Low | Validation | A stop or target inside the entry zone passes | Verified | ❌ NOT STARTED |
| AA-09 | Low | API | `POST /api/ai/jobs` is not rate-limited | Code-read | ❌ NOT STARTED |
| AA-10 | Low | Backend + UI | A model trend that contradicts the engine is only logged | Code-read | ❌ NOT STARTED |
| AA-11 | Low | Backend | A cached result reports its original data age | Verified | ❌ NOT STARTED |
| AA-12 | Low | Context | Peer context falls back to a peer's first available signal | Code-read | ❌ NOT STARTED |
| AA-13 | Low | Backend | The temperature comment contradicts the code | Code-read | ❌ NOT STARTED |
| AA-14 | Low | Cleanup | `AnalyzeRequest` and `record_trade_plan` are dead code | Code-read | ❌ NOT STARTED |
| AA-15 | Low | API | A default template silently replaces the built-in prompt | Code-read | ❌ NOT STARTED |

**Next suggested order:**
1. **Batch 1:** AA-01, AA-02 and AA-03. They are small and fix what the trader
   sees in the panel.
2. **Batch 2:** AA-06, AA-07, AA-08 and AA-11: validation and cache
   correctness.
3. **Batch 3:** AA-04 and AA-05: grading and tracking. They need an issued
   plan id and intraday grading for the tracking day.
4. **Batch 4:** AA-09, AA-10 and AA-12 to AA-15: the remaining low items.

---

## High

### AA-01 — Analysis blocks the server's event loop

**Status:** ❌ NOT STARTED
**Where:** `analyze_symbol` and `analyze_symbol_stream` (`backend/ai/analyze.py:281` and `:558`), called from the `async` routes `POST /api/ai/analyze` and `POST /api/ai/analyze/stream`.

`build_context` is blocking work: a scan, database reads, provider quotes,
and waits on up to 12 thread-pool tasks. These `async` functions call it
directly, so it runs on the event loop. For the whole context build, every
other request stalls, including Chat streams and websockets.
`POST /api/ai/track-trade-plan` does this correctly with `asyncio.to_thread`,
and so does Chat.

**Reproduced:** a ticker coroutine ran alongside `analyze_symbol` with a
0.4 s `build_context`; the ticker froze for the full 0.4 s.

**Fix:** `ctx = await asyncio.to_thread(build_context, ...)` in both
functions.

---

## Medium

### AA-02 — One inconsistent plan level discards the whole analysis

**Status:** ❌ NOT STARTED
**Where:** `TradePlan._check_consistency` (`backend/ai/prompt.py:128`) raises inside `AnalysisResponse` parsing; `_finalize_analysis` (`analyze.py:366`) then returns a `parse_failed` uncertainty.

A Buy with its stop above the entry, or a target below it, is a plan
problem. But it currently throws away the summary, trend and factors too,
and tells the trader the reply "could not be parsed". This contradicts the
promise that the narrative stays when a plan is withheld.

**Reproduced:** a valid reply whose Buy stop sat above the entry returned
`parse_failed` with no narrative.

**Fix:** parse the analysis without `trade_plan` first, then validate the
plan on its own. On failure, keep the narrative, set `trade_plan` to `None`,
and record `trade_plan_validation = {"status": "unavailable", "reason": ...}`
so the panel shows "No validated trade setup".

### AA-03 — Background results drop evidence, validation and context

**Status:** ❌ NOT STARTED
**Where:** the payload built in `analyze_symbol_task` and `_run_direct` (`backend/ai/tasks.py:112` and below). It keeps 13 fields.

A finished background (template) job has none of the following:
- `trade_plan_validation`;
- quote price, source timestamp, data age, data status, provider, session
  and cache status;
- regime, multi-timeframe scores, track record and peer context;
- `uncertainty_reason` and the confidence-calibration fields.

In the panel:
- **Evidence card:** it reads "Source timestamp unavailable" with an unknown
  data status.
- **Verified plans:** they show without the "✓ Validated against quote" line
  and without **Track this setup**.
- **Withheld plans:** they vanish with no "No validated trade setup" note.

The plan itself is still safe: an unvalidated plan is removed before this
point.

**Reproduced:** running the task on a complete result produced a payload
with none of those fields.

**Fix:** serialize with the same function the blocking and streaming routes
use (`_result_to_dict`, plus the template fields), so all three paths
return one shape.

### AA-04 — Grading never sees the day a plan was tracked

**Status:** ❌ NOT STARTED
**Where:** `_grade_row` reads `get_bars(db, symbol, "1d", from_ts=row.created_at)` (`backend/ai/trade_plan_tracker.py:194`).

Daily bars are stamped at midnight. A plan tracked at 10:30 on day D
therefore never has day D graded; the first graded bar is D+1. For a
`scalp` plan (1-day holding window) that means grading the wrong day.

**Reproduced:**
- **Live data:** in the live database, all 18,276 daily bars are stamped at
  00:00.
- **Probe:** with a target hit on the tracking day and a stop hit the next
  day, the plan was graded a loss.

**Fix:** grade the tracking day from intraday bars after `created_at`, then
daily bars from D+1. Simply including day D's daily bar would be wrong the
other way: it also holds price moves from before the plan was tracked.

### AA-05 — Tracked plans aren't tied to an analysis and lose provenance

**Status:** ❌ NOT STARTED
**Where:** `POST /api/ai/track-trade-plan` (`backend/api/ai/router.py:489`) and `record_confirmed_trade_plan`'s defaults (`trade_plan_tracker.py:79`).

- **Not bound to an analysis:** the endpoint revalidates whatever plan the
  client sends against fresh context. It does not check that an analysis
  produced it, so any structurally valid plan is recorded, including a
  hand-edited one.
- **Provenance lost:** rows store `provider="user-confirmed"` and
  `model="AIAnalysisPanel"`, not the model that proposed the plan, and no
  timeframe. An intraday plan is graded on daily bars, where a bar touching
  both stop and target counts as a loss.

The track record built from these rows is shown to the model as "YOUR OWN
past buy/sell calls" and damps its confidence. It is also a subset the
trader chose to track, not a sample of the model's calls.

**Fix:**
- Have the server issue an id for each verified plan it returns (kept for a
  few minutes), and accept tracking only for such an id.
- Record the real provider, model and timeframe, and grade intraday plans
  on intraday bars.
- Describe the track record as "setups you tracked" in the prompt and the
  UI.

### AA-06 — Plan validation ignores the quote's age

**Status:** ❌ NOT STARTED
**Where:** `_plan_validation` (`backend/ai/analyze.py:753`).

Only the provider's status label blocks a plan: STALE, ERROR, UNKNOWN, GAP,
INCOMPLETE or DUPLICATE. A LIVE or DELAYED quote passes however old it is.

**Reproduced:** a plan validated against a DELAYED quote three days old.

**Fix:** during the regular session, require a recent quote (for example 15
minutes). Outside the session, allow the last close but say so in the
validation.

---

## Low

### AA-07 — Transient failures are cached for 45 seconds

**Status:** ❌ NOT STARTED
**Where:** `_cache_result` on the `providers_unavailable` and `parse_failed` paths in `_finalize_analysis` (`analyze.py:392`, `:413`) and `analyze_symbol_stream`.

A second request within 45 s gets the cached failure without trying the
provider. The panel's **Refresh & rerun** bypasses the cache, but these do
not:
- **Analyze** after switching away and back;
- Chat's reanalysis (`chat_actions.py:982`);
- background jobs;
- the digest (`digest.py:212`).

**Reproduced:** over two calls the provider was called once, and the second
call returned the cached `providers_unavailable`.

**Fix:** cache only successful analyses and `insufficient_data`.

### AA-08 — A stop or target inside the entry zone passes

**Status:** ❌ NOT STARTED
**Where:** `TradePlan._check_consistency` (`prompt.py:128`) compares levels with the entry zone's midpoint; `_plan_validation` checks distances only.

**Reproduced:** a Buy with entry 98–104 and stop 99 was accepted and
verified, although a fill at 98.5 would be below its stop.

**Fix:** compare a Buy's stop with the zone's low edge and its targets with
the high edge, and the reverse for a Sell.

### AA-09 — Background jobs are not rate-limited

**Status:** ❌ NOT STARTED
**Where:** `enqueue_job` (`backend/api/ai/jobs.py:78`).

`/analyze`, `/analyze/stream` and `/track-trade-plan` use the AI rate
limiter. Enqueuing a job does not, so it bypasses the limit on AI calls.

**Fix:** add `Depends(check_rate_limit(_ai_limiter))`.

### AA-10 — A contradicting model trend is only logged

**Status:** ❌ NOT STARTED
**Where:** `_log_trend_disagreements`, called at `analyze.py:421`.

When the model says bullish and the engine says downtrend, or the reverse,
the server logs a warning. The trader sees only the model's trend.

**Fix:** return the engine's direction and a disagreement flag, and show
both in the panel.

### AA-11 — A cached result reports its original data age

**Status:** ❌ NOT STARTED
**Where:** the cache hit in `analyze_symbol` / `analyze_symbol_stream` (`analyze.py:268`, `:533`).

A result served from the 45-second cache keeps the `data_age_seconds` it
had when computed, so the evidence card understates the age by up to 45 s.

**Reproduced:** a cached result 0.2 s later reported the original 5.0 s
age.

**Fix:** recompute the age from `source_timestamp` on a cache hit.

### AA-12 — Peer context falls back to a peer's first available signal

**Status:** ❌ NOT STARTED
**Where:** `_correlation_context` (`backend/ai/context.py:800`).

A peer's direction comes from its daily signal, or from its first available
signal when it has no daily one. That is the silent substitution the
primary timeframe no longer does (see [Timeframe correctness](#timeframe-correctness)).
It also ignores the requested timeframe.

**Fix:** use the requested timeframe's signal and skip a peer that lacks it.

### AA-13 — The temperature comment contradicts the code

**Status:** ❌ NOT STARTED
**Where:** `_build_request` (`analyze.py:344`).

The comment says a high-volatility regime gives a *higher* temperature, but
the code lowers it to at most 0.15.

**Fix:** correct the comment.

### AA-14 — Dead code

**Status:** ❌ NOT STARTED
**Where:** `AnalyzeRequest` (`backend/api/ai/router.py:76`); `record_trade_plan` (`backend/ai/trade_plan_tracker.py:43`).

`AnalyzeRequest` is never used, because the routes take query parameters.
`record_trade_plan` has no callers; only its tests use it.

**Fix:** remove both, and their tests.

### AA-15 — A default template silently replaces the built-in prompt

**Status:** ❌ NOT STARTED
**Where:** `_sync_resolve_template` (`backend/api/ai/router.py:200`).

With no `template_id`, `/analyze` uses the active default template. That
analysis then:
- skips the analysis cache;
- skips the track-record and regime prompt framing;
- gets a trade plan only if the template asks for one.

The panel never says a template was used. The live database's only
template ("System Locked") is not set as the default, so this is latent
today.

**Fix:** use a template only when one is chosen explicitly, or show its
name on the result.

---

## Gaps (not bugs)

- **Deployment hardening:**
  - Add an authorization boundary around the server-global AI
    enable/disable setting (`PATCH /api/ai/config`) before supporting
    shared or remote deployments.
  - Add database-level uniqueness or multi-process coordination for
    tracked setups and grading if the app runs across several workers or
    processes.
- **Release QA and evaluation:**
  - Run the documented device and screen-reader manual pass.
  - Run live UI smoke for opt-in private portfolio and Journal flows using
    a sanctioned fixture environment.
  - Run the full portfolio-shock calculation through the sanctioned live
    flow.
  - Expand evaluation of real-model intent and tool choice beyond scripted
    model replies.
  - Require or improve application-owned evidence citations for applicable
    numeric claims.
- **Test coverage:** these gaps are why the findings above weren't caught:
  - **Grading:** tests patch `get_bars`, so `from_ts` is never exercised
    (AA-04).
  - **Background payload:** no test compares its shape with the blocking
    response (AA-03).
  - **Event loop:** no test checks that an analysis leaves it free (AA-01).
  - **Inconsistent plans:** no test covers an otherwise valid reply with an
    inconsistent plan (AA-02).

## Enhancements

1. **Stream in the panel:** `POST /api/ai/analyze/stream` exists, but the
   panel waits on the blocking call with no progress. Streaming the summary
   would show progress.
2. **Show when there's no engine signal:** when the requested timeframe has
   no signal, `trend_state` is empty and the model's trend has no anchor.
   Say so in the evidence card.
3. **Track-record transparency:** show that the record comes from tracked
   setups, its sample size, and a per-model breakdown once AA-05 records the
   model.
4. **Workflow handoffs:**
   - Journal or Notebook directly from an analysis.
   - Risk sizing using the validated entry and stop.
   - An alert for setup invalidation, with the existing confirmation gate.
   - Evidence or report export from the analysis card.

## Verification

### Review probes (2026-09-24)

Run as a throwaway test file under the offline pytest guards, then deleted.
Each asserted the current behaviour, so a pass confirms the finding.

| Probe | Finding | Result |
|---|---|---|
| A 0.4 s `build_context` beside a ticker coroutine | AA-01 | Ticker frozen for 0.4 s |
| Valid reply with a Buy stop above entry | AA-02 | `parse_failed`, narrative lost |
| Background task on a complete result | AA-03 | Validation, evidence and context fields absent |
| Target hit on the tracking day, stop hit the next | AA-04 | Graded a loss |
| DELAYED quote three days old | AA-06 | Plan verified |
| Two calls with the provider unavailable | AA-07 | Provider called once; second call cached |
| Buy with its stop inside the entry zone | AA-08 | Plan accepted and verified |
| Cached result 0.2 s later | AA-11 | Original data age reported |

**Live database checks (read-only):**
- All 18,276 daily bars are stamped at 00:00 (AA-04).
- The only AI template is not set as the default (AA-15).
- Bars exist for every timeframe the API accepts. An early suspicion that
  4h/15m/5m analyses had no bars was wrong.

### Implementation validation at `43e00bf`

- **Backend focused suite:** `163 passed, 17 subtests passed`.
- **AI Analysis panel suite:** `12 passed`.
- **Other checks:** TypeScript compilation, the production frontend build,
  Ruff on the changed Python files, and `git diff --check` all passed.

The wider Phase 5.8 release validation is recorded in
[`phase_5_8_evaluation.md`](phase_5_8_evaluation.md). It includes the full
backend/frontend suites, provider-free Chat evaluation, privacy tests, and
sanctioned private-flow smoke coverage.

## Fix log

No fixes yet.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | AA-01 to AA-15 | ❌ NOT STARTED | — | `docs/Version_5/v5_ai_analysis.md` | 8 probes | Review logged 15 findings. |

---

## Reference

What AI Analysis does and the guarantees it keeps, as implemented at
`43e00bf`. Where a review finding qualifies a statement, the entry is linked.

### What AI Analysis does

AI Analysis combines structured MarketLens context with model-generated
explanation while keeping market facts, freshness, validation, and tracking
under server control. It supports:

- Symbol and timeframe-specific analysis.
- Market regime and multi-timeframe context.
- Peer and sector context.
- Track-record context when historical tracked setups are available.
- Structured trade-plan output for Buy, Sell, Hold, or Avoid.
- Server-authored evidence and freshness information.
- Blocking and bounded background analysis in the panel. SSE streaming exists
  in the API (`POST /api/ai/analyze/stream`) but the panel does not use it.
- Explicit setup tracking after user confirmation.

### Timeframe correctness

Scanner signals use internal engine keys such as `ONE_DAY`, while public AI
Analysis requests use values such as `1d`. The context layer canonicalizes
these values and selects only the requested timeframe. If that timeframe is
missing, it does not silently substitute another signal. Peer context still
does; see AA-12.

### Server-authored evidence

Successful analysis responses expose evidence derived from `AnalysisContext`,
not from model prose:

- Symbol and requested timeframe.
- Quote price.
- Source timestamp and data age.
- Provider and provider data status.
- Market session.
- Cache status.
- Market regime.
- Multi-timeframe direction, strength, and confidence.
- Peer and track-record context where available.

The panel displays this in a compact evidence card, so the trader can see
whether an answer is current, delayed, historical, stale, or cached.
Background (template) results currently lack these fields (AA-03).

### Trade-plan safety gate

Model-proposed Buy/Sell levels are validated after parsing against the
current quote and engine-derived support/resistance. The server withholds a
plan when evidence is missing, errored, unknown, structurally insufficient,
or materially inconsistent with the current market context, or when the
quote's status label is stale.

The server does not invent replacement prices. When the structural checks
fail, the narrative stays, the actionable plan is removed, and the UI shows
`No validated trade setup`.

Limits found by the review:
- **Stale by label only:** "stale" means the provider's status label, not
  the quote's age (AA-06).
- **Inconsistent plans:** a plan that is internally inconsistent currently
  discards the whole analysis (AA-02).
- **Levels inside the entry zone:** a stop or target inside the entry zone
  passes (AA-08).

### User interaction model

Analysis is user-triggered. Changing a symbol or timeframe clears the
previous result and cancels work, but does not contact a provider or record
a setup just because the trader is browsing.

The main panel action is:

- `Analyze` for the first run.
- `Refresh & rerun` for a subsequent fresh run that bypasses the short-lived
  analysis cache.

Requests are abortable and ordered. A late response from an older symbol,
timeframe, or refresh cannot overwrite the current result.

### Explicit setup tracking

Trade-plan outcome tracking is opt-in and requires all of the following:

1. The environment enables trade-plan tracking.
2. The analysis shows a server-verified Buy or Sell plan. The panel checks
   this; the server revalidates the submitted plan but does not check that
   an analysis produced it (AA-05).
3. The trader clicks `Track this setup`.
4. The trader confirms the browser confirmation prompt.
5. The server rebuilds fresh context and revalidates the exact submitted plan.

The endpoint is `POST /api/ai/track-trade-plan`. It records only a verified
actionable plan. Repeated requests for the same open setup return the
existing outcome instead of creating another grading row. Hold, Avoid,
unvalidated, stale, and unavailable plans cannot be tracked. Analysis,
refresh, and background execution do not create outcome rows.

A background thread grades open rows against daily bars: a target hit before
the stop is a win, the stop first is a loss, and neither within the holding
window (scalp 1 day, swing 10, position 60) is expired. See AA-04 and AA-05
for the grading limits.

### Background analysis lifecycle

Background template analysis is coordinated through a typed React imperative
handle rather than a mutable DOM bridge. The lifecycle includes:

- Request ordering guards.
- Abortable enqueue and status requests.
- A deterministic timeout.
- Cancellation and terminal failure states.
- Retry behavior.
- Safe handling of late polling responses.
- Correct persisted job IDs passed to workers.

### Main implementation locations

| Area | Location |
| --- | --- |
| Context and evidence | [`backend/ai/context.py`](../../backend/ai/context.py) |
| Analysis and plan validation | [`backend/ai/analyze.py`](../../backend/ai/analyze.py) |
| Prompt and structured response types | [`backend/ai/prompt.py`](../../backend/ai/prompt.py) |
| AI API routes | [`backend/api/ai/router.py`](../../backend/api/ai/router.py) |
| Background jobs | [`backend/api/ai/jobs.py`](../../backend/api/ai/jobs.py), [`backend/ai/tasks.py`](../../backend/ai/tasks.py) |
| Explicit plan tracking and grading | [`backend/ai/trade_plan_tracker.py`](../../backend/ai/trade_plan_tracker.py) |
| Outcome model | [`backend/models/ai_trade_plan_outcome.py`](../../backend/models/ai_trade_plan_outcome.py) |
| Analysis panel | [`frontend/src/components/AIAnalysisPanel.tsx`](../../frontend/src/components/AIAnalysisPanel.tsx) |
| Analysis API client | [`frontend/src/services/api.ts`](../../frontend/src/services/api.ts) |
| Panel styles | [`frontend/src/styles/App.css`](../../frontend/src/styles/App.css) |

### Related documents

- [`phase_audit_v5.md`](phase_audit_v5.md) — complete Version 5 audit and phase status.
- [`phase_audit_v5_tables.md`](phase_audit_v5_tables.md) — detailed coverage tables.
- [`phase_5_7_manual_qa.md`](phase_5_7_manual_qa.md) — manual accessibility and device QA.
- [`phase_5_8_evaluation.md`](phase_5_8_evaluation.md) — verifier and Chat evaluation.
- [`v5_bug_fixes.md`](v5_bug_fixes.md) — the 2026-09-24 Chat review and its fixes.
