# Version 5 AI Analysis Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (batch 4: AA-09, AA-10, AA-12 to AA-15)
**Status:** Complete. All 15 findings from the 2026-09-24 review are fixed and covered by focused regression tests. See [Reference](#reference) for how AI Analysis works.
**Scorecard:** 15 ✅ COMPLETE, 0 ⚠️ PARTIAL, 0 ❌ NOT STARTED, 0 🟡 DEFERRED.
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
| AA-01 | High | Backend | Analysis blocks the server's event loop while it builds context | Verified | ✅ COMPLETE |
| AA-02 | Medium | Backend | One inconsistent plan level discards the whole analysis | Verified | ✅ COMPLETE |
| AA-03 | Medium | Backend + UI | Background (template) results drop evidence, validation and context | Verified | ✅ COMPLETE |
| AA-04 | Medium | Tracking | Grading never sees the day a plan was tracked | Verified | ✅ COMPLETE |
| AA-05 | Medium | Tracking | Tracked plans aren't tied to an analysis and lose model and timeframe | Code-read | ✅ COMPLETE |
| AA-06 | Medium | Validation | Plan validation ignores the quote's age | Verified | ✅ COMPLETE |
| AA-07 | Low | Backend | Transient failures are cached for 45 seconds | Verified | ✅ COMPLETE |
| AA-08 | Low | Validation | A stop or target inside the entry zone passes | Verified | ✅ COMPLETE |
| AA-09 | Low | API | `POST /api/ai/jobs` is not rate-limited | Code-read | ✅ COMPLETE |
| AA-10 | Low | Backend + UI | A model trend that contradicts the engine is only logged | Code-read | ✅ COMPLETE |
| AA-11 | Low | Backend | A cached result reports its original data age | Verified | ✅ COMPLETE |
| AA-12 | Low | Context | Peer context falls back to a peer's first available signal | Code-read | ✅ COMPLETE |
| AA-13 | Low | Backend | The temperature comment contradicts the code | Code-read | ✅ COMPLETE |
| AA-14 | Low | Cleanup | `AnalyzeRequest` and `record_trade_plan` are dead code | Code-read | ✅ COMPLETE |
| AA-15 | Low | API | A default template silently replaces the built-in prompt | Code-read | ✅ COMPLETE |

**Next suggested order:**
1. ~~**Batch 1:**~~ AA-01, AA-02 and AA-03: done.
2. ~~**Batch 2:**~~ AA-06, AA-07, AA-08 and AA-11: done.
3. ~~**Batch 3:**~~ AA-05 and AA-04: done.
4. ~~**Batch 4:**~~ AA-09, AA-10 and AA-12 to AA-15: done.

---

## High

### AA-01 — Analysis blocks the server's event loop

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `analyze_symbol` and `analyze_symbol_stream` (`backend/ai/analyze.py:281` and `:558`), called from the `async` routes `POST /api/ai/analyze` and `POST /api/ai/analyze/stream`.

`build_context` is blocking work: a scan, database reads, provider quotes,
and waits on up to 12 thread-pool tasks. These `async` functions called it
directly, so it ran on the event loop. For the whole context build, every
other request stalled, including Chat streams and websockets.
`POST /api/ai/track-trade-plan` does this correctly with `asyncio.to_thread`,
and so does Chat.

**Reproduced:** a ticker coroutine ran alongside `analyze_symbol` with a
0.4 s `build_context`; the ticker froze for the full 0.4 s.

**Resolution:** both functions now run the context build with
`await asyncio.to_thread(build_context, ...)`.

**Tests:** in `backend/tests/ai/test_phase16_analyze.py::TestAnalysisLeavesTheEventLoopFree`:
- `test_context_build_does_not_block_other_coroutines`
- `test_streaming_context_build_does_not_block_either`

Both run a ticker beside a 0.4 s context build and require every gap to
stay under 0.25 s. Both fail with the old call.

**Checked live:** a real AAPL analysis on the dev server took 9.3 s. During
it, `/api/health` requests every 0.2 s never took more than 7 ms.

---

## Medium

### AA-02 — One inconsistent plan level discards the whole analysis

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `TradePlan._check_consistency` (`backend/ai/prompt.py:128`) raises inside `AnalysisResponse` parsing; `_finalize_analysis` (`analyze.py:366`) then returns a `parse_failed` uncertainty.

A Buy with its stop above the entry, or a target below it, is a plan
problem. But it also threw away the summary, trend and factors, and told
the trader the reply "could not be parsed". This contradicted the promise
that the narrative stays when a plan is withheld.

**Reproduced:** a valid reply whose Buy stop sat above the entry returned
`parse_failed` with no narrative.

**Resolution:**
- **Plan checked separately:** `parse_ai_reply` now validates the analysis
  without `trade_plan` (`_analysis_from_data` in `prompt.py`), then
  validates the plan on its own.
- **On failure:** the narrative is kept, `trade_plan` is `None`, and
  `trade_plan_validation` is `{"status": "unavailable", "reason": "The
  proposed plan was inconsistent (buy plan: stop_loss must be below the
  entry zone), so no actionable setup was validated."}`. The panel shows it
  as "No validated trade setup".
- **Reason kept:** `_finalize_analysis` keeps that parse-time result
  instead of overwriting it with the structural check.
- **Hardening:** the fields the server stamps after parsing are now dropped
  from the model's JSON. These are provenance, evidence, validation,
  `uncertainty_reason`, `confidence_declared` and the context blocks.
  `AnalysisResponse` declares them, so a model reply could previously set
  some that were never overwritten on success, such as `uncertainty_reason`
  and `confidence_declared`. This also keeps a model from supplying its own
  `trade_plan_validation`, which the fix above now preserves.

**Tests:** in `backend/tests/ai/test_phase16_analyze.py::TestInconsistentPlanKeepsTheNarrative`:
- `test_the_plan_is_withheld_with_a_reason_and_the_analysis_is_kept`
- `test_a_consistent_plan_is_still_validated_against_structure`
- `test_server_owned_fields_in_the_model_reply_are_ignored`

Restoring the in-parse plan validation fails the first test, and allowing
server-owned fields fails the third.

### AA-03 — Background results drop evidence, validation and context

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** the payload built in `analyze_symbol_task` and `_run_direct` (`backend/ai/tasks.py:112` and below). It kept 13 fields.

A finished background (template) job had none of the following:
- `trade_plan_validation`;
- quote price, source timestamp, data age, data status, provider, session
  and cache status;
- regime, multi-timeframe scores, track record and peer context;
- `uncertainty_reason` and the confidence-calibration fields.

In the panel:
- **Evidence card:** it read "Source timestamp unavailable" with an unknown
  data status.
- **Verified plans:** they showed without the "✓ Validated against quote"
  line and without **Track this setup**.
- **Withheld plans:** they vanished with no "No validated trade setup" note.

The plan itself was still safe: an unvalidated plan is removed before this
point.

**Reproduced:** running the task on a complete result produced a payload
with none of those fields.

**Resolution:** both job paths now build their result with `_job_payload`.
It uses the streaming route's serializer (`_result_to_dict`) plus the
template fields, so blocking, streaming and background results have one
shape. The panel needed no change: it already renders these fields.

**Tests:** `backend/tests/ai/test_tasks.py::TestAnalyzeSymbolTask::test_payload_matches_the_blocking_response_shape`
requires the payload to equal the blocking shape. The task tests now build
real `AnalysisResponse` results instead of `MagicMock`s; the mocks had
pinned the narrow payload.

**Deployment note:** the RQ workers that run background jobs don't reload
code the way the dev API server does. The fix reaches background jobs only
after the workers are restarted (for example with `./start.sh`).

### AA-04 — Grading never sees the day a plan was tracked

**Status:** ✅ COMPLETE (2026-09-24, batch 3)
**Where:** `_grade_row` reads `get_bars(db, symbol, "1d", from_ts=row.created_at)` (`backend/ai/trade_plan_tracker.py:194`).

Daily bars are stamped at midnight. A plan tracked at 10:30 on day D
therefore never has day D graded; the first graded bar is D+1. For a
`scalp` plan (1-day holding window) that means grading the wrong day.

**Reproduced:**
- **Live data:** in the live database, all 18,276 daily bars are stamped at
  00:00.
- **Probe:** with a target hit on the tracking day and a stop hit the next
  day, the plan was graded a loss.

**Resolution:** the tracker now reads 1-minute bars from the explicit
confirmation timestamp through the end of day D, then reads daily bars from
D+1. It never grades a daily D bar containing price action that preceded the
tracked setup. A same-day target/stop therefore resolves before later daily
bars can change the result.

**Tests:** `TestGradeRow` proves a post-confirmation 1-minute target wins even
when D+1 would stop out, and that the daily query begins exactly at the next
midnight.

### AA-05 — Tracked plans aren't tied to an analysis and lose provenance

**Status:** ✅ COMPLETE (2026-09-24, batch 3)
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

**Resolution:**
- Each blocking, streaming, and finished background analysis issues an opaque,
  short-lived server-side handle only for a verified Buy/Sell plan. The panel
  sends that handle alone to track a setup; it no longer sends editable symbol,
  timeframe, or plan fields.
- The endpoint returns `410 Gone` if the handle expired or the API restarted,
  so the trader must rerun analysis rather than track a stale or altered plan.
  It rebuilds fresh context and revalidates the retained server plan before
  recording it.
- Outcomes now retain the actual provider, model, requested timeframe, and
  opaque analysis handle. Alembic migration
  `20261001_ai_trade_plan_provenance` adds the durable `timeframe` and
  `analysis_id` columns without rewriting legacy rows.
- Prompt/context and UI call the metric **Tracked setups** and explicitly say
  it is a selected trader-tracked sample, not the model's complete history.

**Tests:** the verified-plan store covers eligibility, expiry, and background
handle reuse; router tests cover issuance, accepted handle provenance, and a
410 expired/unknown handle; the panel hides tracking without a handle. The
migration completeness test checks both persisted provenance columns.

### AA-06 — Plan validation ignores the quote's age

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** `_plan_validation` (`backend/ai/analyze.py:753`).

Only the provider's status label used to block a plan: STALE, ERROR, UNKNOWN,
GAP, INCOMPLETE or DUPLICATE. A LIVE or DELAYED quote passed however old it was.

**Reproduced:** a plan validated against a DELAYED quote three days old.

**Resolution:** during the regular session, a quote must have a known age of
15 minutes or less. Premarket, after-hours, and closed-session plans can use
the latest available quote, but return a visible validation note saying so.

**Tests:** a 901-second DELAYED regular-session quote is withheld; a three-day
HISTORICAL quote while closed remains eligible and carries the off-session
note.

---

## Low

### AA-07 — Transient failures are cached for 45 seconds

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** `_cache_result` on the `providers_unavailable` and `parse_failed` paths in `_finalize_analysis` (`analyze.py:392`, `:413`) and `analyze_symbol_stream`.

A second request within 45 s used to get the cached failure without trying the
provider. The panel's **Refresh & rerun** bypassed the cache, but these did
not:
- **Analyze** after switching away and back;
- Chat's reanalysis (`chat_actions.py:982`);
- background jobs;
- the digest (`digest.py:212`).

**Reproduced:** over two calls the provider was called once, and the second
call returned the cached `providers_unavailable`.

**Resolution:** cache only successful analyses and deterministic
`insufficient_data` results. Provider-unavailable, disabled, and parse-failed
responses retry on the next request.

**Tests:** provider-unavailable and parse-failed calls invoke the provider on
both attempts; `insufficient_data` still reuses its bounded uncertainty result.

### AA-08 — A stop or target inside the entry zone passes

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** `TradePlan._check_consistency` (`prompt.py:128`) compares levels with the entry zone's midpoint; `_plan_validation` checks distances only.

**Reproduced:** a Buy with entry 98–104 and stop 99 was accepted and
verified, although a fill at 98.5 would be below its stop.

**Resolution:** Buy stops must be below the entry zone's low edge and Buy
targets above its high edge; Sell checks use the opposite edges. The parser
withholds an inconsistent plan while keeping the narrative, and the
server-side structural gate retains the same defense in depth.

**Tests:** Buy stop and target values inside a 98–104 entry zone both withhold
the plan with the narrative intact.

### AA-09 — Background jobs are not rate-limited

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `enqueue_job` (`backend/api/ai/jobs.py:78`).

`/analyze`, `/analyze/stream` and `/track-trade-plan` use the AI rate
limiter. Enqueuing a job does not, so it bypasses the limit on AI calls.

**Resolution:** `POST /api/ai/jobs` now uses the same 10-per-minute
per-client AI limiter as blocking analysis, streaming analysis, and setup
tracking. A queued job cannot bypass the AI budget.

**Tests:** eleven enqueue attempts produce ten accepted requests and a 429;
the AI limiter records the rejection.

### AA-10 — A contradicting model trend is only logged

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_enforce_trend_alignment` in `backend/ai/analyze.py`.

When the model says bullish and the engine says downtrend, or the reverse,
the server logs a warning. The trader sees only the model's trend.

**Resolution:** an opposite model direction is now surfaced as `mixed`, its
confidence is capped at 0.50 before any calibration, and a deterministic
timeframe-conflict message identifies the quantitative requested-timeframe
direction. The existing panel renders that conflict instead of presenting the
model's opposite call as clean truth.

**Tests:** an engine downtrend plus a model bullish reply returns `mixed`, a
confidence no higher than 0.50, and the quantitative 1d conflict text.

### AA-11 — A cached result reports its original data age

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** the cache hit in `analyze_symbol` / `analyze_symbol_stream` (`analyze.py:268`, `:533`).

A result served from the 45-second cache kept the `data_age_seconds` it had
when computed, so the evidence card understated the age by up to 45 s.

**Reproduced:** a cached result 0.2 s later reported the original 5.0 s
age.

**Resolution:** a cache hit advances the server-authored age by its elapsed
monotonic cache time in both blocking and streaming paths. It therefore stays
truthful even if a provider timestamp is unavailable or cannot be parsed.

**Tests:** a result cached for ten seconds reports its original five-second
age as approximately fifteen seconds when reused.

### AA-12 — Peer context falls back to a peer's first available signal

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_correlation_context` (`backend/ai/context.py:800`).

A peer's direction comes from its daily signal, or from its first available
signal when it has no daily one. That is the silent substitution the
primary timeframe no longer does (see [Timeframe correctness](#timeframe-correctness)).
It also ignores the requested timeframe.

**Resolution:** correlation context receives the canonical requested
timeframe, uses only that peer signal, and omits peers that do not have it.
`peer_count` now reflects analyzed peers, not merely requested symbols.

**Tests:** a 15-minute analysis omits a peer that only has a daily signal.

### AA-13 — The temperature comment contradicts the code

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_build_request` (`analyze.py:344`).

The comment says a high-volatility regime gives a *higher* temperature, but
the code lowers it to at most 0.15.

**Resolution:** the comment now matches the implementation: sparse context
uses a higher temperature, while rich context and high-volatility/crisis
regimes use lower, more conservative temperatures.

### AA-14 — Dead code

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `AnalyzeRequest` (`backend/api/ai/router.py:76`); `record_trade_plan` (`backend/ai/trade_plan_tracker.py:43`).

`AnalyzeRequest` is never used, because the routes take query parameters.
`record_trade_plan` has no callers; only its tests use it.

**Resolution:** removed the unused request-body model, legacy auto-capture
function, and their obsolete tests. The only outcome write path is explicit
server-verified setup tracking.

### AA-15 — A default template silently replaces the built-in prompt

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_sync_resolve_template` (`backend/api/ai/router.py:200`).

With no `template_id`, `/analyze` uses the active default template. That
analysis then:
- skips the analysis cache;
- skips the track-record and regime prompt framing;
- gets a trade plan only if the template asks for one.

The panel never says a template was used. The live database's only
template ("System Locked") is not set as the default, so this is latent
today.

**Resolution:** analysis and template rendering now use a saved template only
when its ID is explicitly supplied. The library's default marker remains a
UI/library preference and no longer silently changes production analysis.

**Tests:** an analysis resolver with no template ID returns `(None, None)`
without querying templates, preserving the built-in prompt.

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
- ~~**Grading:**~~ batch 3 tests assert the 1-minute `from_ts` boundary and
  the D+1 daily boundary (AA-04).
  - ~~Background payload, event loop, inconsistent plans~~: covered by
    tests since batch 1 (AA-03, AA-01, AA-02).

## Enhancements

1. **Stream in the panel:** `POST /api/ai/analyze/stream` exists, but the
   panel waits on the blocking call with no progress. Streaming the summary
   would show progress.
2. **Show when there's no engine signal:** when the requested timeframe has
   no signal, `trend_state` is empty and the model's trend has no anchor.
   Say so in the evidence card.
3. **Track-record transparency:** the record is now labelled as tracked
   setups and provenance is retained; add a per-model breakdown.
4. **Workflow handoffs:**
   - Journal or Notebook directly from an analysis.
   - Risk sizing using the validated entry and stop.
   - An alert for setup invalidation, with the existing confirmation gate.
   - Evidence or report export from the analysis card.

## Verification

### Batch 3 (2026-09-24)

| Suite | Result |
|---|---|
| Focused backend tracking, API, migration, analysis, and task tests | 202 passed, 17 subtests passed |
| AI Analysis panel suite | 14 passed |
| TypeScript and production frontend build | passed |

**Regression coverage:** opaque verified-plan eligibility and expiry,
blocking-route issuance, background handle reuse, provenance persistence,
same-day post-confirmation 1-minute grading, D+1 daily grading, and hiding
the Track action when the server did not issue a handle.

### Batch 4 (2026-09-24)

| Suite | Result |
|---|---|
| Focused analysis, tracking, router, job, template, rate-limit, migration, and task tests | 246 passed, 17 subtests passed |

**Regression coverage:** background-job AI rate limiting, model/engine trend
conflict surfacing, peer timeframe matching, explicit-template-only analysis,
and removal of obsolete auto-tracking and request-body paths.

### Batch 2 (2026-09-24)

| Suite | Result |
|---|---|
| AI Analysis, task, and AI router tests | 158 passed, 17 subtests passed |
| AI Analysis panel suite | 13 passed |
| TypeScript and production frontend build | passed |
| Ruff and `git diff --check` | clean |

**Regression coverage:** regular-session quote age, closed-session validation
note, transient provider/parse retry, cached-age advancement, and entry-zone
stop/target rejection.

### Batch 1 (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/ai`, `backend/tests/api` | 1,557 passed (6 new), 30 subtests passed |
| `ruff` on the changed files | clean |
| Live analysis on the dev server | 9.3 s; `/api/health` stayed at 7 ms or less during it; evidence present |

**Mutation check:** undoing each change fails its tests:
- the context build back on the loop (2 tests);
- the plan validated inside the analysis;
- server-owned fields accepted;
- the narrow job payload.

Neither full suite was run.

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

- **Review:** commit `783afae`, `docs(v5): review AI Analysis and track findings in the bug-fix format`, on `development`.
- **Batches 1 and 2:** in the working tree, not yet committed.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | AA-01 to AA-15 | ❌ NOT STARTED | review | `docs/Version_5/v5_ai_analysis.md` | 8 probes | Review logged 15 findings. |
| 2026-09-24 | AA-01 | ✅ COMPLETE | batch 1 | `backend/ai/analyze.py`, `backend/tests/ai/test_phase16_analyze.py` | 2 tests | Context build runs in a worker thread. |
| 2026-09-24 | AA-02 | ✅ COMPLETE | batch 1 | `backend/ai/prompt.py`, `backend/ai/analyze.py`, `backend/tests/ai/test_phase16_analyze.py` | 3 tests | Plan validated separately; narrative kept; server-owned fields ignored. |
| 2026-09-24 | AA-03 | ✅ COMPLETE | batch 1 | `backend/ai/tasks.py`, `backend/tests/ai/test_tasks.py` | 1 new, 2 rewritten | Background results use the blocking shape. |
| 2026-09-24 | AA-06 | ✅ COMPLETE | batch 2 | `backend/ai/analyze.py`, `frontend/src/components/AIAnalysisPanel.tsx`, tests | 2 tests + UI | Regular-session age limit; visible off-session note. |
| 2026-09-24 | AA-07 | ✅ COMPLETE | batch 2 | `backend/ai/analyze.py`, `backend/tests/ai/test_phase16_analyze.py` | 3 tests | Retry transient provider and parse failures; retain deterministic no-data cache. |
| 2026-09-24 | AA-08 | ✅ COMPLETE | batch 2 | `backend/ai/prompt.py`, `backend/ai/analyze.py`, tests | 2 tests | Validate stops and targets against entry-zone edges. |
| 2026-09-24 | AA-11 | ✅ COMPLETE | batch 2 | `backend/ai/analyze.py`, `backend/tests/ai/test_phase16_analyze.py` | 1 test | Advance evidence age on cache hits. |

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
Background (template) results carry the same fields since batch 1 (AA-03).

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
- **Regular-session freshness (fixed in batch 2):** a quote must be no more
  than 15 minutes old, while off-session validation labels the latest
  available quote (AA-06).
- **Inconsistent plans (fixed in batch 1):** an internally inconsistent
  plan is now withheld with its reason, and the analysis is kept (AA-02).
- **Entry-zone edges (fixed in batch 2):** stops and targets must clear the
  entire entry zone, not merely its midpoint (AA-08).

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
2. The analysis returns a server-issued, short-lived handle for a
   server-verified Buy or Sell plan. The browser retains this opaque handle,
   not an editable copy of the plan (AA-05).
3. The trader clicks `Track this setup`.
4. The trader confirms the browser confirmation prompt.
5. The server rebuilds fresh context and revalidates the retained,
   server-authored plan. An expired or unknown handle returns `410 Gone` and
   requires a new analysis.

The endpoint is `POST /api/ai/track-trade-plan`. It records only a verified
actionable plan. Repeated requests for the same open setup return the
existing outcome instead of creating another grading row. Hold, Avoid,
unvalidated, stale, and unavailable plans cannot be tracked. Analysis,
refresh, and background execution do not create outcome rows.

A background thread grades open rows against 1-minute bars after the tracking
confirmation on day D, then daily bars from D+1: a target hit before the stop
is a win, the stop first is a loss, and neither within the holding window
(scalp 1 day, swing 10, position 60) is expired. See AA-04 and AA-05 for the
grading limits.

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
