# V5 AI Analysis

**Status:** Core implementation complete
**Last updated:** 2026-09-24
**Latest implementation commit:** `43e00bf`

This document is the focused reference for the Version 5 AI Analysis workflow.
The broader Phase 5 status and release evidence remain in
[`phase_audit_v5.md`](phase_audit_v5.md).

## What AI Analysis does

AI Analysis combines structured MarketLens context with model-generated
explanation while keeping market facts, freshness, validation, and tracking
under server control. It supports:

- Symbol and timeframe-specific analysis.
- Market regime and multi-timeframe context.
- Peer and sector context.
- Track-record context when historical tracked setups are available.
- Structured trade-plan output for Buy, Sell, Hold, or Avoid.
- Server-authored evidence and freshness information.
- Blocking, streaming, and bounded background analysis.
- Explicit setup tracking after user confirmation.

## Trust and correctness guarantees

### Timeframe correctness

Scanner signals use internal engine keys such as `ONE_DAY`, while public AI
Analysis requests use values such as `1d`. The context layer canonicalizes
these values and selects only the requested timeframe. If that timeframe is
missing, it does not silently substitute another signal.

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

The panel displays this information in a compact evidence card so the user can
see whether an answer is current, delayed, historical, stale, or cached.

### Trade-plan safety gate

Model-proposed Buy/Sell levels are validated after parsing against the current
quote and engine-derived support/resistance. The server rejects or withholds a
plan when evidence is missing, stale, errored, unknown, structurally
insufficient, or materially inconsistent with the current market context.

The server does not invent replacement prices. When validation fails, the
narrative can remain available but the actionable plan is removed and the UI
shows a clear `No validated trade setup` state.

## User interaction model

Analysis is user-triggered. Changing a symbol or timeframe clears the previous
result and cancels work, but does not contact a provider or record a setup just
because the user is browsing.

The main panel action is:

- `Analyze` for the first run.
- `Refresh & rerun` for a subsequent fresh run that bypasses the short-lived
  analysis cache.

Requests are abortable and ordered. A late response from an older symbol,
timeframe, or refresh cannot overwrite the current result.

## Explicit setup tracking

Trade-plan outcome tracking is opt-in and requires all of the following:

1. The environment enables trade-plan tracking.
2. The analysis contains a server-verified Buy or Sell plan.
3. The user clicks `Track this setup`.
4. The user confirms the browser confirmation prompt.
5. The server rebuilds fresh context and revalidates the exact submitted plan.

The tracking endpoint is:

```text
POST /api/ai/track-trade-plan
```

The endpoint records only a verified actionable plan. Repeated requests for the
same open setup return the existing outcome instead of creating another grading
row. Hold, Avoid, unvalidated, stale, and unavailable plans cannot be tracked.

Analysis, refresh, and background execution do not create outcome rows.

## Background analysis lifecycle

Background template analysis is coordinated through a typed React imperative
handle rather than a mutable DOM bridge. The lifecycle includes:

- Request ordering guards.
- Abortable enqueue and status requests.
- A deterministic timeout.
- Cancellation and terminal failure states.
- Retry behavior.
- Safe handling of late polling responses.
- Correct persisted job IDs passed to workers.

## Main implementation locations

| Area | Location |
| --- | --- |
| Context and evidence | [`backend/ai/context.py`](../../backend/ai/context.py) |
| Analysis and plan validation | [`backend/ai/analyze.py`](../../backend/ai/analyze.py) |
| Prompt and structured response types | [`backend/ai/prompt.py`](../../backend/ai/prompt.py) |
| AI API routes | [`backend/api/ai/router.py`](../../backend/api/ai/router.py) |
| Explicit plan tracking | [`backend/ai/trade_plan_tracker.py`](../../backend/ai/trade_plan_tracker.py) |
| Outcome model | [`backend/models/ai_trade_plan_outcome.py`](../../backend/models/ai_trade_plan_outcome.py) |
| Analysis panel | [`frontend/src/components/AIAnalysisPanel.tsx`](../../frontend/src/components/AIAnalysisPanel.tsx) |
| Analysis API client | [`frontend/src/services/api.ts`](../../frontend/src/services/api.ts) |
| Panel styles | [`frontend/src/styles/App.css`](../../frontend/src/styles/App.css) |

## Validation completed

The explicit setup-tracking implementation was validated with:

- Backend focused suite: `163 passed, 17 subtests passed`.
- AI Analysis panel suite: `12 passed`.
- TypeScript compilation: passed.
- Production frontend build: passed.
- Ruff checks on changed Python files: passed.
- `git diff --check`: passed.

The wider Phase 5.8 release validation is recorded in
[`phase_5_8_evaluation.md`](phase_5_8_evaluation.md) and includes the full
backend/frontend suites, provider-free Chat evaluation, privacy tests, and
sanctioned private-flow smoke coverage.

## Remaining work

The core AI Analysis trust and correctness gaps are closed. Remaining work is
follow-up enhancement or release QA:

### Product workflow enhancements

- Direct handoff from an analysis to Journal or Notebook.
- Risk-sizing handoff using the validated entry and stop.
- Alert creation for setup invalidation, with the existing confirmation gate.
- Evidence/report export directly from the analysis card.

### Deployment hardening

- Add an authorization boundary around the server-global AI enable/disable
  setting before supporting shared or remote deployments.
- Add database-level uniqueness or multi-process coordination if the app is
  deployed across multiple workers/processes.

### Release QA and evaluation

- Run the documented device and screen-reader manual pass.
- Run live UI smoke for opt-in private portfolio and Journal flows using a
  sanctioned fixture environment.
- Run the full portfolio-shock calculation through the sanctioned live flow.
- Expand evaluation of real-model intent and tool choice beyond scripted
  model replies.
- Require or improve application-owned evidence citations for applicable
  numeric claims.

These items do not block the completed Phase 5.8 implementation gate, but they
should be tracked before calling the product fully production-ready for shared
deployment.

## Related documents

- [`phase_audit_v5.md`](phase_audit_v5.md) — complete Version 5 audit and phase status.
- [`phase_audit_v5_tables.md`](phase_audit_v5_tables.md) — detailed coverage tables.
- [`phase_5_7_manual_qa.md`](phase_5_7_manual_qa.md) — manual accessibility and device QA.
- [`phase_5_8_evaluation.md`](phase_5_8_evaluation.md) — verifier and Chat evaluation.
