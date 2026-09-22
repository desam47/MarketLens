# Version 5 Phase Audit

**Last updated:** 2026-09-22 (re-scoped from Charts to Intelligent AI Hub Chat)
**Status:** Active. Planning complete; implementation not started.
**Scope:** Grounded tool-using Chat, verified calculations, market/user-data retrieval, bounded orchestration, analysis workflows, structured UI, personalization, and reliability evaluation.
**Branch workflow:** Version 5 implementation is developed on `development`; `main` remains the protected stable branch and receives reviewed merges only.

---

## Scorecard

| # | Phase | Status | Notes |
|---|---|---|---|
| 5.1 | Tool foundation and safe calculator | ⬜ NOT STARTED | Typed tools, deterministic calculations, session/time/unit normalization, permissions, canonical metric catalog. |
| 5.2 | Grounded market-data tools and provenance | ⬜ NOT STARTED | Market/user tools, evidence, conflict reconciliation, application help, safe local imports. |
| 5.3 | Bounded orchestration, intent, and memory | ⬜ NOT STARTED | Limited tool loop, clarification, state, decomposition, reusable workflows, model routing and budgets. |
| 5.4 | Analysis, comparisons, scenarios, and explanations | ⬜ NOT STARTED | Why/what changed, rankings, scenarios, similarity, counterarguments, sensitivity, timelines, anomalies and assumptions. |
| 5.5 | Scanner, watchlist, alerts, and briefings | ⬜ NOT STARTED | Natural-language filters, watchlist intelligence, alert conversations, scheduled summaries. |
| 5.6 | Trade planning, risk, options, and journal coaching | ⬜ NOT STARTED | Verified plans, portfolio risk, options, journal analytics, save/export and decision checklists. |
| 5.7 | Structured Chat UI and personalization | ⬜ NOT STARTED | Typed UI, preferences, chart state, navigation, feedback, regeneration, notebooks and answer refresh. |
| 5.8 | Reliability, evaluation, and release hardening | ⬜ NOT STARTED | Answer verification, hallucination controls, evaluation, audit trail, fallbacks, performance and release gate. |

---

## Baseline verified before Version 5

The existing Chat implementation is not a blank slate. The following
capabilities are already present and must be preserved throughout the migration:

- Universal and symbol-scoped sessions with persisted conversation history.
- Per-turn ticker resolution, market baseline, pruned symbol context, and streaming replies.
- Provider fallback through the existing AI manager.
- Fresh symbol reanalysis.
- Create/modify/delete alerts with confirmation on destructive actions.
- Create/delete watchlists and add/remove/reclassify watchlist symbols.
- Fixed six-month backtest tool.
- Natural-language watchlist screening through the existing scanner parser/executor.
- Deterministic handling for several watchlist queries and confirmations.
- Grounded/partial/unavailable symbol provenance in the frontend.

These are baseline behavior, not completed Version 5 items. Version 5 begins
when the typed general-purpose tool foundation in Phase 5.1 is implemented.

---

## Phase 5.1 — Tool foundation and safe calculator

Not started. Planned items 5.1.1–5.1.4 and their verification criteria are in
`docs/Version_5/v5_plan.md`. Audit this phase with formula-level test results,
separate per-share/total/portfolio-risk outputs, verified-result formula
references, tool schema coverage, invalid-input behavior, and proof that
arbitrary code execution is impossible. The canonical metric catalog must also
be checked against the owning page/API for formula, units, timeframe/session
behavior, and terminology consistency.

---

## Phase 5.2 — Grounded market-data tools and provenance

Not started. Audit must record each tool's source API/service, cache behavior,
provider-call impact, freshness fields, delayed/fallback handling, and contract
tests against existing page APIs. The audit must explicitly account for the
quote, bars, indicator, support/resistance, regime, market-context, sector,
news, fundamentals, options, watchlist, alerts, journal, and risk tools named
in the plan. It must also cover conflict precedence, application-help links,
and safe local CSV validation with inert formulas/macros.

---

## Phase 5.3 — Bounded orchestration, intent, and memory

Not started. Audit must record enforced call/time/token limits, clarification
coverage, repeated-call protection, structured memory fields—including the
previous ticker—and destructive action confirmation tests. Complex-request
decomposition, partial failure, saved workflows, model routing, and deterministic
AI-off fallback must be exercised explicitly.

---

## Phase 5.4 — Analysis, comparisons, scenarios, and explanations

Not started. Audit must include golden numerical fixtures, aligned-timeframe
comparison tests, historical leakage checks, and examples that distinguish
facts, correlation, inference, and uncertainty. Counterarguments, invalidation,
sensitivity, event ordering, anomaly baselines, and assumption-staleness tests
are required.

---

## Phase 5.5 — Scanner, watchlist, alerts, and briefings

Not started. Audit must prove generated Scanner filters match executed filters,
session boundaries are preserved, alert trigger values are exact, and scheduled
summaries are deduplicated and timestamped. Weekly-review tests must cover
performance, recurring mistakes, plan-versus-execution differences, and setup
strengths/weaknesses when sufficient Journal data is available.

---

## Phase 5.6 — Trade planning, risk, options, and journal coaching

Not started. Audit must trace every numerical output to calculator results,
verify missing-input clarification, preserve delayed options labels, and
reproduce journal analytics from stored records. Options coverage must include
calls, puts, defined-risk spreads, and IV percentile. Decision-checklist tests
must distinguish completed, failed, unavailable, and skipped checks.

---

## Phase 5.7 — Structured Chat UI and personalization

Not started. Audit must list every response block, persistence version,
accessibility test, responsive-layout test, preference location, and migration
behavior for older prose-only messages. Every data-backed block must be checked
for its evidence-derived confidence/data-quality state. Personalization tests
must cover day-trading, swing-trading, options, and long-term-investing modes
without changing verified calculations or source evidence. Chart-state,
state-preserving navigation, feedback classification, regeneration, notebooks,
and stale-answer refresh must each have persistence and UI tests.

---

## Phase 5.8 — Reliability, evaluation, and release hardening

Not started. Audit must publish the evaluation categories and results, failure
matrix, tool/provider request counts, latency measurements, security checks,
full automated test results, and manual smoke-test outcomes. The answer verifier
must demonstrate detection of altered numbers, unsupported claims, wrong units,
wrong sessions/timeframes, and contradictions between prose and cited evidence.
