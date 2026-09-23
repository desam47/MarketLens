# Version 5 Phase Audit

**Last updated:** 2026-09-22 (re-scoped from Charts to Intelligent AI Hub Chat)
**Status:** Active. Planning complete; Phase 5.1 complete; Phase 5.2 in progress.
**Scope:** Grounded tool-using Chat, verified calculations, market/user-data retrieval, bounded orchestration, analysis workflows, structured UI, personalization, and reliability evaluation.
**Branch workflow:** Version 5 implementation is developed on `development`; `main` remains the protected stable branch and receives reviewed merges only.

**Current checkpoint (2026-09-22):** Version 4's implemented scope is
merged to `main`. The `development` branch is synchronized with its remote
and is the only branch receiving new Version 5 work. Phase 5.1 is complete;
Phase 5.2 is the active delivery gate.

**Latest delivery (commit `9ceedec`):** Phase 5.1's first implementation
slice is now shipped on `development`. The strict calculator foundation in
`backend/ai/calculator.py` is exposed through the read-only
`POST /api/ai/calculate` endpoint, returns formulas and assumptions, and
supports the documented change, return, risk, portfolio, volatility,
drawdown, correlation, and options calculations. Seven focused calculator
tests pass with `DEBUG=false`; no changes have been merged to `main`.

The follow-up registry slice adds `ToolRequest`, `ToolResult`, `ToolSpec`, and
`ToolRegistry` in `backend/ai/tool_registry.py`. The default registry exposes
only the named `calculate` tool, rejects unknown tools/fields, records duration
and warnings, and includes canonical session, timeframe, percentage, and metric
metadata helpers. Three registry tests pass in addition to the calculator tests
(10 focused tests total).

The protocol slice now also routes `/api/ai/calculate` through that registry,
adds UTC execution timestamps and normalized session/timeframe context to every
result, and provides `verified_formula.py` for restricted arithmetic over
verified result fields. Calls, attributes, indexing, imports, and unknown
values are rejected. Five formula/protocol tests pass, bringing the focused
Phase 5.1 total to 15.

The Chat integration slice registers `calculate` as a closed action with a
validated nested `CalculationRequest`. Chat now receives backend-verified
values and formulas instead of performing arithmetic in model prose. The
metric catalog was expanded with scanner, microstructure, signal, trend,
confluence, regime, risk, options, and performance identifiers. Registry
permissions, per-tool rate limits, provider/source timestamps, and freshness
metadata are now part of the result envelope. Seventeen focused Phase 5.1
tests pass in total.

A conservative deterministic fallback now handles unambiguous allocation,
percentage/dollar change, reward/risk, and position-size wording when the AI
returns no action. It constructs the same validated calculator request and
never guesses when required inputs are missing. The focused Phase 5.1 suite
now has 18 passing tests.

A closing verification pass (2026-09-22) added the previously missing
`options_assignment_exposure` calculation and made `ToolSpec.permission`
an enforced gate (`mutating` tools require `confirmed=True`) instead of an
unread field. The focused Phase 5.1 suite now has 24 passing tests; the full
`backend/tests/ai/` suite passes (548 tests) with no regressions.

**Phase 5.1 completion:** The planned tool foundation, safe calculator,
normalization, registry/permissions (now enforced), restricted verified
formulas, initial metric catalog, Chat calculation action, provenance
metadata, and focused verification are complete. Broader market-data tools
and richer structured provenance cards belong to Phase 5.2 and later phases.

---

## Scorecard

| # | Phase | Status | Notes |
|---|---|---|---|
| 5.1 | Tool foundation and safe calculator | ✅ COMPLETE | Calculator (incl. assignment exposure), typed envelope, normalization, enforced registry permissions/rate limits, restricted formulas, metric catalog, Chat action, provenance metadata, and 24 focused tests are complete. |
| 5.2 | Grounded market-data tools and provenance | 🟡 IN PROGRESS (~60%) | Market, research, watchlist, risk, journal, alerts, and sector-data tools are registered with typed provenance; 4 tools have contract tests against their page/API equivalents. Missing: trend/confluence/microstructure tools, catalyst/earnings/analyst tools, CSV import, and dynamic app-help. |
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

These are baseline behavior, not completed Version 5 items. Version 5 work is
now active: the typed tool foundation and calculator are implemented on
`development`, while the remaining phases are still pending.

---

## Phase 5.1 — Tool foundation and safe calculator

The first safe-calculator slice is implemented in
`backend/ai/calculator.py`, with a strict Pydantic request schema, named
operations, formula/assumption metadata, and no dynamic evaluation. It covers
change, return/CAGR, weighted averages, position sizing, reward/risk,
allocation, volatility, drawdown, correlation, and basic options metrics.
`POST /api/ai/calculate` exposes the calculation as a read-only endpoint.
Seven focused tests pass with `DEBUG=false`; the full suite remains subject to
the repository's existing Alembic test-database initialization prerequisite.

The options calculation set now also includes `options_assignment_exposure`
(assignment shares and cash exposure from strike, contracts, and a
configurable contract multiplier), closing the gap against the plan's
breakeven/intrinsic/extrinsic/max-gain-loss/assignment-exposure list.

Tool permission is now enforced, not just declared: `ToolSpec.permission`
accepts `read_only`, `calculation`, or `mutating`, and `ToolRegistry.execute`
rejects a `mutating` tool call unless the request carries `confirmed=True`.
No mutating tool is registered yet — this closes the enforcement gap ahead of
Phase 5.3/5.6 work that will add write-capable tools.

Audit this phase with formula-level test results, separate
per-share/total/portfolio-risk outputs, verified-result formula references,
tool schema coverage, invalid-input behavior, mutating-tool confirmation
gating, and proof that arbitrary code execution is impossible.

---

## Phase 5.2 — Grounded market-data tools and provenance

The first read-only tool slice is implemented in `backend/ai/market_tools.py`
and registered through the shared registry: `get_quote`, `get_bars`,
`get_indicator`, `get_support_resistance`, `get_market_regime`,
`get_market_context`, `get_news`, `get_fundamentals`, `get_options_snapshot`,
`get_watchlist`, `get_risk_dashboard`, `get_trade_journal`, `get_alerts`, and
`get_application_help`. Bar and
indicator tools reuse the existing manager/cache
path, preserve provider/session/timeframe metadata, and never write to the
database. The shared registry now derives actual provider, source timestamp,
freshness age, fallback state, and stale/delayed warnings from each tool
payload. Chat can select each tool as a bounded read-only action and includes
the verified payload provenance in its response. Fourteen focused market-tool,
registry, and Chat integration tests pass with `DEBUG=false`.

The shared registry now includes provider-observation reconciliation: a
configured primary wins when present, otherwise the newest observation wins,
and material differences produce an explicit conflict warning without making
duplicate provider calls. The quote tool now records the manager's selected
primary/fallback provider and a no-conflict single-observation reconciliation
state without issuing a second quote request. Seven registry/provenance tests
plus the quote contract pass in this slice.

Watchlist retrieval is database-backed and read-only. Risk Dashboard and Trade
Journal currently keep their manual records in browser `localStorage`, so the
new tools accept explicit snapshots and return a truthful unavailable response
when the server cannot see browser-local state; they never invent positions or
journal entries. Focused market-tool, registry, and Chat tests cover these
contracts and deterministic risk summaries.

`get_alerts` is database-backed through `AlertRepository`, closing the
explicitly-named 5.2.4 gap. It supports symbol scoping, enabled-only
filtering, and optionally attaches each alert's recent triggers (bounded by
`trigger_limit`). One focused test creates alerts through the repository and
asserts the tool's exact row shape.

`get_sector_data(symbol)` is implemented, closing the explicitly-named 5.2.2
gap. It reuses the existing `SectorEngine` via `backend.api.regime.router`'s
shared, DB-seeded engine cache (the same one `/regime/{symbol}/sector`
serves), returning sector, sector ETF, stock/sector/market trend agreement,
and an alignment score/level — no new computation, no new provider calls.
One focused test covers it. The 5.2 focused suite is now 16 tests; the full
`backend/tests/ai/` suite passes at 550 tests (554 including the new
contract-test file), and the full backend suite passes at 2806 tests.

Remaining items named in the plan are still unimplemented and not yet
reflected as done anywhere in this document: dedicated
trend/confluence/relative-strength/BBO/tape-pressure/large-prints/
session-statistics tools (5.2.2) — only generic sma/ema/rsi indicators exist
today, not MarketLens's own trend/confluence/microstructure engines wrapped
as tools; dedicated catalyst/earnings/insider-activity/analyst-recommendation
tools (5.2.3) — `get_news`/`get_fundamentals` do not cover these; and CSV
import tools (5.2.8), which have no code at all yet.
Application-help (5.2.7) is a hardcoded 12-page table, not the
route/feature-metadata-backed, deep-link-capable tool the plan describes.

`backend/tests/ai/test_tool_contracts.py` now covers the phase's own stated
verification bar for four tools: `get_sector_data` vs
`GET /api/regime/{symbol}/sector`, `get_market_context` vs
`GET /api/market-context/current`, `get_watchlist` vs
`GET /api/watchlists/{id}` + `/symbols`, and `get_alerts` vs
`GET /api/alerts/`. Each test mounts the real router (the
`test_regime_api.py` pattern) and asserts the tool and the page/API return
the same values for the same symbol/scope, not just plausible-looking ones.

This surfaced two real bugs, not just missing coverage, both now fixed:
1. `get_market_regime_tool` and `get_market_context_tool` called
   `signal.model_dump(mode="json")` on plain Python objects — `RegimeSignal`
   is a bare class with no `to_dict`/`model_dump` at all, and
   `MarketContextSignal` is a dataclass with `to_dict()`, not `model_dump()`.
   Both would raise `AttributeError` on the happy path (a signal actually
   present), not just when unwarmed — no existing test exercised that path.
   Fixed by reusing the same field-by-field serialization
   (`_data_age_seconds`/`_freshness`/`_to_dashboard_tz`) the endpoints
   themselves use, imported from `backend/api/regime/router.py` and
   `backend/api/market_context/router.py`.
2. Both tools also raised `ValueError` when the engine had no signal yet
   (cold start / unwarmed), while their equivalent endpoints return a
   graceful `200` with an honest `"unknown"`/`"no_data"` state. The tools
   now return that same honest-unknown payload instead of erroring —
   consistent with the plan's "graceful uncertainty when evidence is
   missing" principle (top-level Goal 6), not a fabricated value.
3. (Already fixed in the `get_alerts` slice, confirmed by the same pass.)
   `get_alerts_tool` serialized `created_at`/`updated_at` as naive
   `.isoformat()` while the alerts API uses `format_edt_iso` to attach an
   explicit NY offset — the project-wide convention (naive datetimes are NY
   local; without the offset, browser/JS code misreads them as local time).
   Fixed to use `format_edt_iso` for consistency.

Remaining tools without a contract test: `get_quote`/`get_bars` (would
require a live/mocked provider, not exercised yet), `get_indicator`/
`get_support_resistance` (derived from bars, no dedicated page endpoint to
compare against), `get_news`/`get_fundamentals`/`get_options_snapshot`
(aux-data provider paths, not yet compared against their page renderings),
`get_risk_dashboard`/`get_trade_journal` (no server-side equivalent to
compare against by design — browser-local data), and `get_application_help`
(no equivalent API endpoint exists).

Remaining work includes wiring multi-observation reconciliation into provider
paths that expose multiple observations, complete freshness/fallback contracts
for every tool, and safe local import handling. The application-help tool now returns verified page
titles, feature topics, and current hash routes from the navigation catalog.
Audit must record each tool's source API/service, cache behavior,
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
