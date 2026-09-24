# Version 5 Phase Audit

**Last updated:** 2026-09-24 (Chat prose-format hardening)
**Status:** Active. Planning complete; Phases 5.1–5.8 are complete. Version 5 release handoff to the protected stable branch remains outside this phase audit.
**Scope:** Grounded tool-using Chat, verified calculations, market/user-data retrieval, bounded orchestration, analysis workflows, structured UI, personalization, and reliability evaluation.
**Branch workflow:** Version 5 implementation is developed on `development`; `main` remains the protected stable branch and receives reviewed merges only.

**Current checkpoint (2026-09-23):** Version 4's implemented scope is
merged to `main`. The `development` branch is the only branch receiving
Version 5 work. Phases 5.1–5.8 are implemented, with the plan deviations
and open gaps listed in [Known gaps and plan deviations](#known-gaps-and-plan-deviations-2026-09-23-review).
The 2026-09-23 Chat review found and fixed safety/verification bugs; see
[Chat review fixes](#chat-review-fixes-2026-09-23). For Phase 5.2, the
Post-completion review explains the two items (multi-provider
reconciliation, further contract-test expansion) that are blocked on real
architectural gaps rather than left undone.

The registry now has 42 tools and `ChatReplyResponse.action` accepts 53
actions (registry tools plus the pre-Version-5 alert/watchlist/backtest/
screen/reanalysis actions). Tool counts quoted in the historical slice notes
below (14, 20, 22, 23) were correct when written.

**Phase 5.1 delivery history (commit `9ceedec`):** Phase 5.1's first implementation
slice shipped on `development`. The strict calculator foundation in
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
| 5.2 | Grounded market-data tools and provenance | ✅ COMPLETE | 23 tools registered, every tool named in 5.2.1–5.2.8 implemented, with consistent freshness/fallback/entitlement fields and a generated (not hand-copied) application-help route table. 8 of 23 tools have live contract tests — traced to be close to the practical ceiling for this codebase (see 2026-09-22 review). Multi-provider reconciliation stays unit-tested infrastructure — no real multi-observation path exists to wire it into without a deliberate architecture change. |
| 5.3 | Bounded orchestration, intent, and memory | ✅ COMPLETE | Bounded chaining, budgets, duplicate suppression/reuse, persistent memory and confirmations, deterministic intent routes, visible step decomposition, reusable workflows, role-specific model routes, and AI-off evidence-only fallback are implemented and tested. |
| 5.4 | Analysis, comparisons, scenarios, and explanations | ✅ COMPLETE | The typed analysis tools provide evidence, baseline comparisons, bounded rankings, deterministic what-if outputs, look-ahead-safe historical samples, signal review, conditional sensitivity outputs, normalized event timelines, anomaly baselines, and an assumption ledger with immutable originals, source/creation provenance, stale/broken status transitions, and explicit unknowns. |
| 5.5 | Scanner, watchlist, alerts, and briefings | ✅ COMPLETE | 5.5.1 Natural-language Scanner Builder, 5.5.2 Watchlist Intelligence, 5.5.3 Alert-to-conversation, 5.5.4 Scheduled Summaries, and 5.5.5 What-changed Inbox are complete. The local AI Hub inbox uses a browser checkpoint, reads durable watchlist/alert/signal/provider activity, deduplicates repeated events, preserves timestamps/severity/source links, and does not trigger provider polling. |
| 5.6 | Trade planning, risk, options, and journal coaching | ✅ COMPLETE | 5.6.1–5.6.4 and 5.6.6 are complete. `build_trade_plan`, `assess_portfolio_risk`, `options_research`, `trade_journal_coach`, and `decision_checklist` use verified calculator/tool evidence and honest unavailable states. 5.6.5 validates and saves an approved typed Journal entry through a server-enforced confirmation gate, returns a bounded local snapshot for browser persistence, exports verified plans/reviews as local Markdown reports, and exposes Symbol, Scanner, Risk, Replay, Alerts, and Journal deep links rendered as Chat actions. |
| 5.7 | Structured Chat UI and personalization | ✅ COMPLETE (with gaps) | Typed blocks, visual/action cards, preferences, answer-contract metadata, feedback classification, chart state, context-preserving navigation (including Options/System Health), regeneration with timeframe/session scope, server-backed notebooks with local fallback, regression-fixture promotion, and age/material-change freshness are implemented. Accessibility/responsive coverage is automated only; the manual device pass has not been run. Regeneration modes and per-block quality are partial — see Known gaps. |
| 5.8 | Reliability, evaluation, and release hardening | ✅ COMPLETE (with gaps) | Verification, sanitized observability, bounded fallback matrix, performance decision, security/privacy review, sanctioned private-flow smoke, public live smoke, and the release gate are complete. The scored evaluation exercises the answer verifier only, not routing/planning; destructive and portfolio flows were not smoke-tested live. See Known gaps. |

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

These are baseline behavior, not completed Version 5 items.

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
The Phase 5.6.5 `save_to_journal` tool is registered as mutating and adds a
server-authored Chat confirmation gate before the browser-local Journal state
changes.

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
`backend/tests/ai/` suite passes at 577 tests (575 prior + 2 for
get_calendar), and the full backend suite passes at 2831 tests.

`get_trend`, `get_confluence`, `get_relative_strength`, and `get_tape_state`
are now implemented, closing most of 5.2.2's named engine list (trend,
multi-timeframe confluence, relative strength, BBO, tape pressure, large
prints — session-statistics is not covered). Each reuses the real engine
registry and, where the endpoint had one, the endpoint's own payload
builder rather than re-deriving the response shape:
- `get_trend` calls `backend.api.trend.router._build_trend_payload` — the
  exact function `GET /api/trend/{symbol}/current/{timeframe}` uses.
- `get_confluence` calls a newly-extracted `build_confluence_payload`,
  factored out of `GET /api/multitimeframe/{symbol}/confluence`'s
  previously-inline logic into `backend/api/multitimeframe/router.py`
  specifically so the endpoint and the tool cannot diverge — the endpoint
  itself was refactored to call the same function; 35 existing MTF/
  confluence tests confirm the refactor is behavior-preserving.
- `get_relative_strength` reuses `backend.api.regime.router._get_rs_engine`
  and each signal's own `.to_dict()`, matching
  `GET /api/regime/{symbol}/relative-strength`.
- `get_tape_state` reuses `backend.api.tape.registry.get_tape_engine(...)
  .get_snapshot()` directly (the same one-line body
  `GET /api/tape/{symbol}` has) and surfaces the same
  `TAPE_ENABLED=false` condition as an ordinary tool error instead of an
  unhandled exception.

Five focused tests cover the cold-engine ("no signal yet") path for each,
plus the tape-disabled and tape-enabled paths.

`test_tool_contracts.py` now also covers `get_trend`, `get_confluence`, and
`get_relative_strength` against their live endpoints (`GET
/api/trend/{symbol}/current/{timeframe}`, `GET
/api/multitimeframe/{symbol}/confluence`, `GET
/api/regime/{symbol}/relative-strength`) — whatever state the shared engine
registry is in when the test runs (cold in the sandboxed DB, or warm from
an earlier test in the same session), tool and endpoint must describe it
identically, since both read the same engine instance. This is now 7 of 20
tools with a live contract test (sector, context, watchlist, alerts, trend,
confluence, relative-strength). `get_tape_state` has a live contract test
only for its disabled-feature path — the enabled/live-snapshot path was
deliberately left to the mocked-engine unit test instead, because
`get_tape_engine()` with `seed=True` (the only mode either the endpoint or
the tool use) schedules a background-thread Webull seed
(`backend/api/tape/registry.py::_schedule_seed`) that the sandboxed test
environment's network guard is not a safe target for.

Remaining tools without any contract test: `get_quote`/`get_bars` (would
need a live/mocked provider), `get_indicator`/`get_support_resistance`
(derived from bars, no dedicated page endpoint), `get_news`/
`get_fundamentals`/`get_options_snapshot` (aux-data provider paths), `get_risk_dashboard`/`get_trade_journal` (no
server-side equivalent by design), and `get_application_help` (no
equivalent API endpoint).

`import_csv` (5.2.8) is now implemented, closing that item. It parses
local CSV text (never a file path — the same explicit-snapshot pattern
`get_risk_dashboard`/`get_trade_journal` already use, since Chat has no
file-upload path) via the stdlib `csv` module only, for three import
types: positions (validated into the same `PositionInput` shape
`get_risk_dashboard` accepts), watchlist symbols (deduplicated, uppercased),
and trade-journal rows (loose dict rows, matching `get_trade_journal`'s
own loose schema). Bounded to 500 rows / 40 columns / 500 characters per
cell; required columns are checked per import type before parsing rows
(missing `symbol`/`quantity`/`entry_price` for `positions` fails the whole
import rather than silently dropping rows); per-row numeric/type errors are
collected and returned alongside successfully parsed rows rather than
aborting the whole import. Formula-injection-style cells (`=cmd|...`,
`+SUM(...)`, etc.) are proven inert by test — the parser never opens the
content in a spreadsheet engine, only `csv.reader`, so such a cell is just
a string starting with `=`. The tool only parses and validates; nothing is
persisted — a caller wanting the parsed rows actually saved passes them to
the existing tool/UI that does that (e.g. `get_risk_dashboard`'s own
explicit-snapshot argument), the same separation `get_risk_dashboard`/
`get_trade_journal` already use. 8 new focused tests cover valid parsing
per type, deduplication, missing-required-column and invalid-numeric-value
error reporting, the row-count cap, headerless mode's honest failure (a
`symbol` column name is still required, `column_1`/`column_2`/... will
never satisfy it), and the formula-injection-is-inert guarantee.

`get_session_stats` (5.2.2) is now implemented, completing that item's
engine list. No dedicated page endpoint exists to contract-test against
(same category as `get_indicator`/`get_support_resistance`), so it derives
O/H/L/C, volume, VWAP, and range directly from 1-minute bars already
fetched through the shared manager/cache. It scopes to the most recent
trading day present in the bars, then to the requested session
(premarket/regular/after_hours/all) using each bar's own per-bar `session`
classification — never re-deriving session boundaries independently, so it
cannot disagree with what the bars themselves already say. Returns an
honest `available: false` with a reason when no bars exist for the
requested session (e.g. asking for after-hours data on a day with none)
rather than fabricating a zero-filled result. 4 new tests cover date/session
scoping, an "all sessions" combined view, and the unavailable case.

`get_calendar` (5.2.3) is now implemented, reusing
`backend.market_data.services.calendar_service.events_for_symbol` — the
same function `GET /api/calendar/symbol/{symbol}` calls, including its
7-day-lookback/180-day-lookahead window and TTL cache. On re-reading the
plan's exact wording ("catalysts, earnings, insider activity, analyst
recommendations, and sector context") against the actual data model:
insider ownership and analyst recommendation/target were already present
in `FundamentalsItem` (`insider_ownership`, `institutional_ownership`,
`analyst_target`, `recommendation`) and therefore already reachable through
`get_fundamentals` before this slice — they were never a real gap, just an
undercounted one in earlier phase-audit passes. `get_calendar` closes what
was actually missing: catalyst/earnings *events* (dates), which nothing
else exposed. Sector context was already covered by `get_sector_data`.
A contract test caught a real, if minor, shape mismatch: the endpoint
wraps each raw event dict through `CalendarEvent(**event)`, whose
`source: str = "yfinance"` default fills in a field `events_for_symbol`
itself never sets; the tool returned the raw dicts unmodified, without
that field. Fixed to match. 1 unit test plus 1 contract test (both
patching the same underlying function, since real yfinance calls aren't
safe in the sandboxed test environment) cover it.

Every tool explicitly named across 5.2.1–5.2.8 is now implemented.

## Post-completion review (2026-09-22) — the remaining four polish items

A follow-up review produced a four-item punch list: (1) complete
freshness/fallback/entitlement fields consistently across every tool, (2)
wire multi-provider reconciliation into real paths that expose multiple
observations, (3) expand contract tests beyond 8 of 23 tools, (4) replace
the hand-maintained application-help route table with a generated
manifest. Investigating each against the actual code before touching
anything found that two of the four rest on an assumption the codebase
doesn't support — documented here so the assumption doesn't get
re-proposed without new evidence, per this file's own stated audit
convention (see the relative-strength composite-index note above).

**(1) Freshness/fallback/entitlement — done.** Added `ToolResult.entitlement`
(`verified`/`declared`/`configured`/`not_applicable`), derived centrally in
`ToolRegistry.execute()` by a new `_entitlement_status()` classifier that
reuses System Health's entitlement vocabulary in simplified form (no
runtime observation-history cross-reference — a single tool call only has
the provider name to go on). `not_applicable` covers every internal
`"MarketLens ..."` label; there is no subscription to lack for a local
calculator, database read, or composite engine signal.

Re-auditing all 22 tool handlers for `provider`/`fallback` (not just
trusting the earlier pass) found 7 tools silently defaulting to the
generic `"MarketLens"` label with no way to tell "explicitly composite"
from "nobody set this": `get_market_regime` and `get_sector_data` now pull
the real provider from their underlying `TrendEngine`'s warmed metadata
(the same data `get_trend` already surfaced); `get_confluence`'s provider
is now part of `build_confluence_payload` itself, so the real endpoint
gains it too, not just the tool; `get_relative_strength` and
`get_market_context` explicitly report the composite-engine label
`"MarketLens engine"` (multiple benchmark/index symbols — no single
provider name would be honest); `get_tape_state` reports `"webull"` (the
only source tape data can come from); `import_csv` reports `"MarketLens
local parser"`. `fallback` was previously only ever set by `get_quote`;
now `get_bars` (and `get_indicator`/`get_support_resistance`/
`get_session_stats`, which inherit its payload), `get_trend`,
`get_confluence`, `get_market_regime`, `get_sector_data`, `get_news`,
`get_fundamentals`, and `get_options_snapshot` all compute it against
their category's configured primary provider.

A contract-test regression caught a real bug introduced while fixing
`get_trend`: substituting `"MarketLens engine"` for a cold engine's
`None` provider broke parity with `GET /api/trend/.../current/...`,
which returns a literal `null` in that case — exactly the kind of
tool-vs-endpoint divergence contract tests exist to catch. Fixed by
leaving the payload's provider exactly as the shared builder set it and
computing `fallback` locally instead. 8 new/extended tests cover the
entitlement classifier's three real states plus not-applicable, registry
propagation, and the new fields on regime/context/sector/tape/news.

**(2) Multi-provider reconciliation wiring — genuinely blocked, not
neglected.** Traced every provider-facing path (`MarketDataManager.get_quote`,
the bars/backfill paths, System Health's `/providers` endpoint). **No path
in this codebase currently produces multiple simultaneous data-value
observations from different providers for the same symbol.** The
architecture is fallback-chain-then-return-one by design —
`get_quote()` iterates providers in priority order and returns on the
first success; it never queries two providers concurrently to compare
them. System Health's `/providers` endpoint reports per-provider health
(latency, error state), not data values, so it isn't reconcilable in the
`reconcile_observations()` sense either. Wiring this in for real would
mean adding a genuinely new capability — querying 2+ providers
concurrently just to compare them, at real extra cost/rate-limit
pressure — which is a deliberate architecture change, not a drive-by
fix. Decision (2026-09-22): leave `reconcile_observations()` as
unit-tested infrastructure for whenever a real multi-provider path
exists; do not fabricate one to have something to wire it into.

**(3) Contract-test expansion — hit the same kind of wall, also
documented rather than padded.** Checked every one of the remaining 15
tools for a genuine live endpoint to compare against, including one
almost-mistake: `get_quote_tool` looks like it should have one
(`GET /api/market-data/quote/{symbol}`), but that endpoint reads
`ingestion_service.get_latest_quote()` — a DB-backed ingestion cache —
while the tool calls `market_data_manager.get_quote()` directly, a live
provider call with its own fallback chain. Different code paths sharing
the word "quote"; a test comparing them would validate nothing real.
Systematically for the rest: `get_bars`/`get_indicator`/
`get_support_resistance` have no dedicated range-bars listing endpoint;
the only news/fundamentals endpoints that exist
(`backend/api/finnhub/router.py`) call `FinnhubService` directly,
bypassing the `AuxDataManager` fallback chain the tools use — different
code path, different response shape; `get_risk_dashboard`/
`get_trade_journal` have no server-side equivalent by design
(browser-local data); `get_session_stats`/`import_csv`/
`get_application_help` have no dedicated page endpoint; `get_tape_state`'s
live path carries real background-thread network risk, already
deliberately left to a mocked unit test. The 8 tools already covered
(sector, context, watchlist, alerts, trend, confluence,
relative-strength, calendar) appear to be close to the practical ceiling
for this kind of test in the current codebase — decision (2026-09-22):
document this rather than write comparisons that would create false
confidence.

**(4) Application-help's route table — done, and no longer a judgment
call.** Replaced the hand-maintained Python route/title table with
`_parse_frontend_hash_by_page()` and `_parse_frontend_page_titles()`,
which read `frontend/src/utils/appNavigation.ts` and `frontend/src/App.tsx`
directly and regex-parse `HASH_BY_PAGE` and each `case 'X': return
...pageName="Y"...` pair — the two places those values actually live in
the frontend, not a Python copy of them. `topics` (keyword-matching) and
`required_state` stay hand-maintained by design: they're this tool's own
domain, with no frontend counterpart to generate from or drift against.
Falls back to a small embedded snapshot (explicitly labeled
`"fallback_snapshot"`, surfaced as a response warning) if the frontend
source files are unreadable, e.g. a backend-only deployment — never a
silent empty result. Parsing genuinely caught a second real drift bug
while building this, independent of the earlier route-hash one: the
hand-maintained table's title for the "signals" page was `"Historical
Replay"`; App.tsx's actual `pageName` prop is `"Historical Signals"`. 4
new tests cover the parsers reading the real files directly, the title
fix, and the fallback path when the frontend source is unavailable.

Full backend suite after all four: 2843 passed.

Application-help (5.2.7) now carries a `required_state` field per page and
parses canonical hashes and page titles directly from the frontend source.
`topics` and `required_state` remain intentionally domain-owned metadata;
the route/title values no longer have a hand-copied Python source of truth.
The parser has a labeled fallback snapshot for backend-only deployments and
tests guard the canonical frontend route set.

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

(Contract-test coverage as of this slice is summarized further down, after
the trend/confluence/relative-strength/tape tools section, rather than
duplicated here.)

The post-completion review records the remaining polish decisions: the
reconciliation helper remains unit-tested infrastructure because no real
multi-observation provider path exists, and contract coverage is intentionally
limited to the page/API equivalents that can be exercised safely in this
localhost build. The generated application-help tool returns verified page
titles, feature topics, and current hash routes. Audit records each tool's
source API/service, cache behavior,
provider-call impact, freshness fields, delayed/fallback handling, and contract
tests against existing page APIs. The audit must explicitly account for the
quote, bars, indicator, support/resistance, regime, market-context, sector,
news, fundamentals, options, watchlist, alerts, journal, and risk tools named
in the plan. It must also cover conflict precedence, application-help links,
and safe local CSV validation with inert formulas/macros.

---

## Phase 5.3 — Bounded orchestration, intent, and memory

The first orchestration slice is implemented in `backend/ai/chat.py`: compound
requests can chain one-action decisions with configurable, bounded planning
budgets (originally three steps; since 2026-09-23 the plan's independent
5 tool-call / 2 planning-call / token budgets — see "Per-turn budgets"
below), duplicate action signatures stop safely, and
continuation failures never discard already completed results. Destructive
actions remain backend-confirmed. Per-turn planner state tracks the original
request, completed steps, duplicate signatures, and errors without persisting
private orchestration data into the conversation transcript. The focused Chat
action suite passes 111 tests with `DEBUG=false`; the broader Chat/settings
slice passes 155 tests.

Structured conversational memory now persists bounded current symbols,
watchlist, timeframe, session, last question, and update timestamp on each
chat session. Ambiguous follow-ups that refer to multiple remembered symbols
receive a deterministic clarification question before any provider or AI call.
Compound replies now label completed subtasks as ordered steps and report when
a dependent continuation cannot be planned safely.
The current assistant response also exposes a transient tool trace with tool
name, success/failure, provider, freshness, fallback, and warnings; it is
rendered in Chat but intentionally not persisted in the prose message table.

Phase 5.3 is complete. Read-only duplicate actions now reuse a bounded
per-turn result cache and are marked as
`turn-cache`; mutating duplicates still stop without re-execution.
Structured memory now includes the previous ticker, last calculation inputs,
and a bounded last-tool-result summary; exact calculations and missing
calculation inputs are handled deterministically before an AI call. Pending
destructive confirmations are persisted with their target and resolved only
against that exact action. High-confidence options, historical, scanner, risk,
journal, and alert requests now route to typed tools before model action
selection. Chained planning
now also observes a configurable wall-clock budget and stops safely before
starting another continuation when it expires. Structured step events now
surface ordered completion, failure, reuse, and stop states with dependency
metadata in the Chat UI. Reusable workflows now have persisted typed steps,
four safe built-in templates, editable parameters, and a confirmation-aware
run endpoint. Role-specific planning, synthesis, and repair routes are
configurable, selected routes are shown in transient Chat metadata, and AI-off
turns fall back to evidence-only context snapshots. Phase 5.3 is now complete;
Phase 5.4 is complete: its evidence, comparison, scenario, explanation,
anomaly, and assumption-tracking slices are implemented and tested.

---

## Phase 5.4 — Analysis, comparisons, scenarios, and explanations

The `why_did_it_move`, `what_changed`, `compare_symbols`, `scenario_analysis`,
`historical_similarity`, `signal_explanation`, `counterargument_review`,
`sensitivity_analysis`, `market_event_timeline`, `anomaly_analysis`, and
`assumption_tracking` slices are implemented and tested. Audit includes golden
numerical fixtures, aligned-timeframe comparison tests, historical leakage
checks, examples that distinguish facts, correlation, inference, and
uncertainty, counterarguments, invalidation, sensitivity, event ordering,
anomaly baselines, immutable assumption history, contradiction-to-broken
transitions, and stale-assumption tests.

---

## Phase 5.5 — Scanner, watchlist, alerts, and briefings

**5.5.1 complete — Natural-language Scanner Builder.** The Scanner page accepts
a plain-English request and calls `POST /api/nl-search/preview`. The endpoint
returns the exact typed `FilterSpec[]` payload and `AND`/`OR` match mode used by
`POST /api/scanner/filter`; it validates every standard filter through the
same registry as execution. Previewing neither runs a scan nor makes a market
data provider request. Selecting **Use editable filters** opens the existing
Filter Builder with that exact payload, so the user can inspect or change it
before the standard debounced Scanner request executes. Supported technical,
session, volume, relative-strength, earnings-exclusion, and microstructure
phrases are deterministic; unsupported news-catalyst language is explicitly
marked unresolved rather than fabricated.

Focused backend tests cover direct translations, every generated filter's
registry compatibility, unknown wording, and the API payload contract.
Focused frontend tests verify the preview is non-executing and hands the exact
payload to the editor. The focused backend suite passes 120 tests and the
Scanner frontend test suite plus production build pass.

**5.5.3 complete — Alert-to-conversation workflow.** Alerts and Signal Alert
Center now hand off a fired trigger through a one-shot session-storage envelope
to AI Hub. AI Hub loads the read-only
`GET /api/alerts/triggers/{id}/conversation-context` snapshot and opens an
alert-scoped chat with the triggering symbol. The response keeps the persisted
rule, observed value, message, timestamp, recent trigger history, current
symbol context, chart/signal explanation, market backdrop, and provider/data
status separate. Missing provider sections are surfaced as warnings rather
than guessed. The existing chat backend also embeds the alert rule and trigger
facts in the model prompt, so the first follow-up has the actual trigger as
grounding. Creating or modifying alerts still uses the closed action set and
server-side confirmation for destructive changes.

Focused backend API tests cover verified context assembly and missing-trigger
404 behavior; the alert and chat frontend suites pass, and the production
frontend build succeeds.

**5.5.4 complete — Scheduled summaries.** The digest scheduler now produces
local premarket, midday, post-market, and Friday weekly summaries. The API and
AI Hub Digest card expose all four sessions, while every payload records its
summary kind, cutoff, period window, and deterministic dedupe key. Durable
slot checks prevent duplicate generation after restarts. The Weekly tab adds
performance, win rate, net P&L, plan coverage, strongest/weakest symbols, and
recurring review themes from the browser-local Trade Journal when entries are
available, and labels that source as local/non-broker-synced. No external
delivery was introduced.

**5.5.5 complete — What-changed Inbox.** AI Hub now exposes a local,
read-only change feed with a browser-local last-visit checkpoint. The bounded
`GET /api/ai/changes` endpoint reads durable watchlist, alert, historical
signal, provider-status, and already-recorded catalyst-source activity; it
never polls providers. Signal and provider events are deduplicated, each item
retains category, severity, symbol, timestamp, and a source-page link, and
warnings explicitly distinguish source activity from verified catalyst data.
Focused API tests cover deduplication, category coverage, source links, and an
empty future checkpoint. The remaining Phase 5.5 work is complete.

**5.5.2 complete — Watchlist Intelligence.** The Watchlist page now performs
one normal Scanner request, then requests a deterministic briefing endpoint
which only reads that current scanner cache. It never triggers another scanner
or market-data provider request. For an explicit premarket, regular, or
after-hours filter, it reuses the established local one-minute session-bar
baseline so mover percentages match the Watchlist table. The typed response
includes bullish/bearish movers, current 20-bar breakouts, deterioration,
volume spikes, benchmark-relative strength, multi-timeframe confirmation, and
watchlist-sector momentum. It reports warming/partial coverage, price basis,
and missing selected-session bars instead of concealing data gaps. Corporate
events use the existing cached provider-estimated calendar lookup once per
watchlist; the UI explicitly declines to infer a news catalyst without a
verified source.

Focused backend tests cover selected-session ranking, signals, sector coverage,
empty/no-session behavior, and the no-second-scan API guarantee. Focused
frontend tests cover the grounded session display, symbol navigation, existing
Watchlist behavior, and production compilation.

---

## Phase 5.6 — Trade planning, risk, options, and journal coaching

**5.6.1 complete — Trade-plan builder.** `build_trade_plan`
(`backend/ai/market_tools.py`) takes symbol, direction, entry (a single
price or an explicit zone), stop, one or more targets, and optionally
account_value + risk_percent, and returns entry zone/reference, per-target
risk/reward, position size, an invalidation sentence, and the caller-supplied
catalysts/risks/timeframe/session, structured and bounded rather than
invented. Every reward:risk and position-size number is produced by calling
`backend.ai.calculator.calculate()` (`risk_reward`, `position_size`) — the
tool performs no arithmetic of its own, satisfying this phase's "all
monetary and percentage values trace to calculator results" verification
rule directly. A missing stop or target raises rather than defaulting one,
per the phase's "missing stop/target/capital produces clarification, not
invented defaults" rule; a stop/target inconsistent with the stated
direction (e.g. a long with the stop above entry) also raises. Position
sizing is independent of the rest of the plan: without account_value and
risk_percent the tool still returns the verified entry/stop/target/
reward-risk plan, with an honest `position_size_reason` instead of a guessed
share count. The tool only builds and returns the plan for the user to
review — saving it to the Journal or creating alerts from it are separate,
explicitly-confirmed actions (5.6.5 `save_to_journal`, implemented below). 8 focused tests cover
the happy path (incl. an entry-zone/short-direction variant), the
missing-sizing-inputs honest-unavailable path, default and caller-supplied
invalidation text, and both required-field and direction-consistency
rejections.

Wiring `build_trade_plan` into Chat surfaced a real, pre-existing bug
affecting Phase 5.2 tools, not just this one: `ChatReplyResponse.action`
(`backend/ai/prompt.py`) is a strict Pydantic `Literal` gating every value
Chat can construct or the model can select — and it was missing
`get_alerts`, `get_sector_data`, `get_trend`, `get_confluence`,
`get_relative_strength`, `get_tape_state`, `get_session_stats`,
`get_calendar`, and `import_csv`. These are real, registered,
individually-tested tools (per the Phase 5.2 section above) that Chat could
never actually select — the model's own JSON would fail schema validation
if it tried, and worse, `backend/ai/chat.py`'s deterministic
`_ALERTS_TOOL_INTENT` branch unconditionally constructs
`ChatReplyResponse(action="get_alerts", ...)`, which raised
`ValidationError` on every single message matching that phrase (e.g. "show
my alerts"). No existing test caught this because tool-level tests
(`test_market_tools.py`, `test_tool_registry.py`, `test_tool_contracts.py`)
exercise each tool directly or through `ToolRegistry`, never through
`ChatReplyResponse` — so "the tool is implemented and tested" and "Chat can
actually reach the tool" had silently diverged. Fixed by adding all nine
plus `build_trade_plan` to the Literal and to both prompt-doc tool
descriptions (so the model knows the newly-reachable tools exist and what
arguments they need). A new standing regression guard,
`backend/tests/ai/test_chat_action_schema.py`, asserts every
`_MARKET_TOOL_ACTIONS` member is both a valid `ChatReplyResponse.action`
value and actually constructible, and that every registered read-only tool
is present in `_MARKET_TOOL_ACTIONS` — so this exact class of "registered
but unreachable" gap fails a test immediately for any future tool, instead
of sitting undetected until a specific phrase happens to trigger the crash.

**5.6.2 complete — Portfolio and Risk Dashboard assistant.**
`assess_portfolio_risk` (`backend/ai/market_tools.py`) takes the same
explicit browser-local position snapshot as `get_risk_dashboard` (required
— never assumed) and explains concentration (top position, top-3 weight,
top sector), sector exposure, stop-loss risk, volatility, pairwise
correlation, and portfolio-level maximum drawdown, plus scenario results
when price shocks are supplied. It reuses `get_risk_dashboard_tool` for
exposure/sector/stop-risk and `scenario_analysis_tool` for the what-if
shock results directly — neither is re-derived. Volatility, correlation,
and drawdown are new: they need each held symbol's own price history,
which (unlike the positions themselves) is not browser-local, so the tool
fetches it itself via `get_bars_tool`, bounded to the 10 largest positions
by weight to cap provider calls. Portfolio drawdown is computed from a
synthetic equity curve (`Σ quantity_i × close_i(t)` across held symbols on
shared trading days), not per-symbol — a real, if unlabeled, portfolio
metric no existing tool produced. Optionally sizes one proposed new trade
against the portfolio's risk capacity (calculator `position_size`, same as
`build_trade_plan`) and, given `risk_limits` (max position/sector percent),
refuses — never silently shrinks — a size that would breach one, returning
`recommended_size: null` with the specific breach reason; without
`risk_limits` it returns the computed size with an explicit note that no
limit was checked. Missing entry/stop/account_value/risk_percent on the
proposed trade also yields `recommended_size: null` with the specific
missing field, matching this phase's "recommend no trade size when
required inputs or risk limits are missing" rule directly. 6 focused tests
cover the honest-unavailable path, concentration/sector/correlation/
volatility/drawdown together (two symbols with identical price series,
which makes correlation exactly 1.0 and drawdown exactly 0% — deterministic
assertions, not approximate ones), both proposed-trade missing-input paths,
an unconstrained sizing, and a max-position-limit breach. Registered as
`assess_portfolio_risk` (read-only) and reachable from Chat via the same
`_MARKET_TOOL_ACTIONS` / `ChatReplyResponse.action` wiring the 5.6.1 fix
put in place.

**5.6.3 complete — Options research assistant.** `options_research`
(`backend/ai/market_tools.py`) fetches a real chain via the existing
`get_options_tool` (never a second/parallel data path) and reports the
chain-level context the plan asks for directly from the data model that
already carried it: IV rank, near-term IV, per-chain avg call/put IV,
put/call ratio, call/put volume, and unusual activity
(`backend/models/aux_data.py`'s `OptionsResponse`/`OptionsChain` already had
all of these — Phase 5.2's `get_options_snapshot` just hadn't had a reason
to surface them yet). Adds expected move (calculator `expected_move`, given
a live quote and the chain's own IV) and a `near_expiration_risk` flag
(`days_to_expiration <= 7`) computed from the chain's real expiration date,
not a guess.

For explicit `legs` (expiration/strike/option_type), each must resolve to a
real contract in the fetched chain or it's reported in `unknowns`, never
invented; the price used for the math is always labeled `premium_source`
("last" or "mid_bid_ask") so it's traceable and never implied as
executable, per this phase's own "never imply executable prices" rule.
Given a resolved price it computes breakeven (`options_breakeven`) and,
when a live underlying price is available, intrinsic/extrinsic value and
max gain/loss (`options_intrinsic_value`/`options_extrinsic_value`/
`options_max_gain_loss`) — all via the existing calculator, skipped rather
than computed with a fabricated underlying price when a quote isn't
available.

For defined-risk vertical `spreads` (a long leg + a short leg, same
expiration and option type), added a genuinely new calculator operation,
`options_vertical_spread` (`backend/ai/calculator.py`), rather than doing
spread arithmetic ad hoc in the tool — the phase's calculator-traceability
rule applies to two-leg structures exactly as much as single legs. Its four
sign-convention branches (call vs. put × which leg holds the higher strike)
were each derived from the expiration payoff function and
verified against textbook bull-call-debit / bear-call-credit /
bear-put-debit / bull-put-credit examples in
`backend/tests/ai/test_calculator.py` before the tool was built on top of
it. The tool also reports the short leg's assignment exposure
(`options_assignment_exposure`) alongside each spread. A mismatched
option_type between the two legs, or either leg failing to resolve, is
reported in `unknowns` rather than computed incorrectly.

8 focused tool tests (unavailable-without-a-chain, chain summary +
expected move, a resolved leg's mid-price/breakeven/intrinsic/extrinsic,
the last-price fallback when no bid/ask exists, an unmatched leg reported
honestly, a verified bull-call-debit spread, a rejected mismatched-type
spread, and the near-expiration flag) plus 6 calculator-level spread tests
plus a registry-level test. Registered as `options_research` (read-only)
and wired into Chat's action set/schema using the same path the 5.6.1 fix
put in place.

**5.6.4 complete — Trade Journal coach.** `trade_journal_coach`
(`backend/ai/market_tools.py`) takes the same loose, browser-local entry
snapshot `get_trade_journal` already accepts (no fixed schema — Journal
entries are arbitrary dicts) and computes win rate, expectancy (average
realized P&L per trade), and average R-multiple (P&L ÷ initial planned
risk, using each entry's own `stop_price`/`planned_stop`) strictly over
closed entries with a usable `entry_price`, `exit_price`, and `quantity`;
anything else lands in `skipped_entries` with a stated reason rather than
being defaulted or silently dropped. Groups the same statistics per `setup`/
`strategy` tag (untagged entries form their own group, never excluded).
Compares plan vs. actual per entry — `exit_classification` (`hit_or_beat_target`
/ `hit_planned_stop` / `exceeded_planned_stop` / `closed_early` / `unknown`)
derived directly from `planned_stop`/`planned_target` against the real
`exit_price` — and surfaces four recurring-pattern **observations**, each a
plain count with the affected symbols, never framed as advice: no stop was
ever recorded, the stop was blown through, the trade closed before its
planned target, and the position was meaningfully larger (>20%) than what
`calculate(position_size)` implies the entry's own stated `account_value`/
`risk_percent` would size — reusing the calculator rather than re-deriving
sizing math, same as `build_trade_plan`/`assess_portfolio_risk`. Each
plan-vs-actual row also reports an `evidence_attached` flag
(`signals`/`market_conditions`/`calculations`/`plan`) — since entries are
untyped dicts, attaching that evidence (the plan's first 5.6.4 bullet) was
already possible; this makes it visible per entry rather than silently
ignored. Assumptions explicitly state the output is observations from the
supplied data, not trading advice, so Chat frames its reply accordingly
(the phase's "distinguish observations from advice" rule) — coaching prose
is Chat's job on top of this evidence, the same separation
`signal_explanation`/`counterargument_review` already use. 8 focused tool
tests (unavailable-without-entries, the full win-rate/expectancy/R-multiple
computation across 7 mixed entries, per-setup grouping, all four
observation types firing correctly — including a `TSLA` entry that
legitimately lands in both `skipped_entries` *and* `no_stop_defined` since
those are independent gaps — plan-vs-actual with evidence flags, and
symbol/setup filtering) plus a registry-level test. Registered as
`trade_journal_coach` (read-only) and wired into Chat's action set/schema
via the same path the 5.6.1 fix put in place.

**5.6.6 complete — Configurable decision checklist.** `decision_checklist`
(`backend/ai/market_tools.py`) evaluates seven named pre-plan checks
against real evidence from the existing tools — never re-derived, never
guessed — each landing in exactly one of the plan's four required states:

- `trend_alignment` — `get_trend_tool`'s direction (`uptrend`/`downtrend`)
  compared against the stated trade direction; `unavailable` on a cold
  (unwarmed) engine rather than a false pass/fail.
- `catalyst_review` — `get_calendar_tool`'s events within a configurable
  window (`catalyst_window_days`, default 7); any event in range is
  `failed` (near-term catalyst risk), none is `completed`.
- `defined_stop` — `completed` iff `stop_price` was supplied.
- `verified_position_size` — `completed` via the same `calculate(position_size)`
  call `build_trade_plan`/`assess_portfolio_risk` already use;
  `unavailable` (not "failed") when entry/stop/account_value/risk_percent
  aren't all present, since there's nothing to verify.
- `earnings_risk` — `get_calendar_tool`'s earnings events between now and
  a supplied option leg's own expiration; `unavailable` without an
  `option_leg` (N/A for an equity trade), matching `options_liquidity`'s
  same gate.
- `options_liquidity` — reuses `options_research_tool`'s own
  `_find_option_contract` matcher against a real fetched chain; open
  interest/volume against configurable minimums.
- `data_freshness` — the quote's own age against a configurable
  `max_data_age_seconds` (default 60s).

Any check not listed in `required_checks` (defaults to all seven) is
reported `skipped` — the caller configuring which checks matter for a
given trade, per the plan's own wording — never silently omitted from the
response. The top-level `ready` field is true only when zero *required*
checks failed; `unavailable`/`skipped` checks remain visible in the
response rather than being treated as passing. 4 focused tool tests (all
seven checks passing together, a mixed failed/unavailable scenario,
narrowing `required_checks` to one check with the rest correctly reported
`skipped`, and a thin-liquidity `failed` case) plus a registry-level test.
Registered as `decision_checklist` (read-only) and wired into Chat's
action set/schema via the same path the 5.6.1 fix put in place.

**5.6.5 complete — Save/export workflows.** `save_to_journal` validates a
typed plan/review entry, requires a server-authored confirmation prompt (the
model cannot self-authorize the mutation), and returns the saved entry for
the browser-local Journal to merge without overwriting existing entries.
`export_report` re-runs the selected verified plan/risk/options/journal tool
or wraps already-rendered custom text, formats a bounded Markdown report,
and returns local-only provenance plus deep links for Symbol, Scanner, Risk,
Replay, Alerts, and Journal. Chat persists the typed save result, renders
the report with Download Markdown and Copy controls, and turns each deep
link into an in-app navigation action. Focused verification covers the
confirmation/replay path, typed report/save blocks, five market-tool tests,
20 registry tests, 131 chat-action/intent tests, 28 ChatPanel tests, and a
successful production build.

---

## Phase 5.7 — Structured Chat UI and personalization

**5.7.1 complete — Typed response blocks (2026-09-23).** Chat
assistant messages now carry an application-owned, Pydantic-validated block
envelope alongside the legacy prose `content`. The envelope supports prose,
calculation, evidence, warning, suggested-follow-up, action-confirmation,
comparison-table, and ranked-result block types. Every block carries
evidence-derived quality metadata (`verified`, `partial`, `unavailable`,
`stale`, or `unknown`) with grounding, provider, source timestamp, freshness,
session, timeframe, and fallback state where available. Calculation blocks are
populated from the verified calculator result rather than model arithmetic.
Blocks are persisted in `chat_messages.response_blocks` through migration
`20260927_chat_response_blocks`, returned by both blocking and streaming Chat
contracts, and rendered without rerunning tools. Rows created before the
migration remain valid and continue to render their original prose.

Focused verification: two backend response-block tests, 26 ChatPanel tests,
and a successful frontend production build. The Alembic upgrade to head was
also verified on a fresh in-project SQLite database.

**5.7.1 gap closure (2026-09-23).** All three items previously listed as
remaining are now done. `ranked_results` blocks are emitted for
`get_relative_strength` (ranked by `rs_pct` against each benchmark, missing
values sorted last rather than dropped) and `anomaly_analysis` (ranked by
`|z_score|`, threshold-triggered anomalies with no z-score sorted after the
scored ones), alongside the existing `compare_symbols` `comparison_table`.
`ChatPanel` now has direct render coverage for every one of the 17 block
types that existed at the time (18 now, with the 5.8 `verification` block) (previously 4 of 17 — `calculation`, `warning`, `report`,
`journal_save`), plus an edge-case test covering an empty chart, null
indicator values, ragged comparison-table rows, a 5,000-character report
body, and an empty ranked list. A new Chat API contract test exercises the
real JSON persistence round trip on `GET .../messages`: every prior
historical-message test left `response_blocks` as an implicit mock
attribute that `_message_to_response`'s `json.loads` rejects and silently
swallows, so the actual persisted-JSON path had never been exercised;
separate tests now also cover a NULL `response_blocks` column (pre-5.7.1
rows) and a corrupted JSON string, both degrading to an empty block list
rather than a 500. "Mobile" here means component-level robustness against
missing/long/ragged data (what can actually crash a component); real
viewport/CSS layout continues to rely on existing responsive CSS
(`overflow-x: auto` table wrappers, an `auto-fit` indicator grid, and
`white-space: pre-wrap` on report bodies), which this test suite does not
exercise.

Focused verification: 9 new `_visual_trace_payload` tests, 3 new Chat API
contract tests, and 2 new ChatPanel tests (2987 backend / 168 frontend
tests passing overall).

**5.7.2 visual/action slice (2026-09-23).** Bounded visual
payloads from bars, indicators, options, risk, scenarios, session statistics,
historical outcomes, and symbol comparisons can now become typed blocks
without persisting full tool responses. Chat renders mini price charts,
indicator tables, options cards, risk/scenario/session cards, historical
outcome tables, comparison/ranked results, and evidence quality labels. Chat
also exposes safe UI shortcuts for Open Symbol, Open Scanner, and Save to
Journal; Save to Journal creates a browser-local draft and never claims that a
trade was recorded before the user submits it. Navigation changes page state
only and cannot execute a market action.

Focused verification: 3 backend response-block tests, 29 Chat/Journal
component tests, and a successful production build. Missing/long-value
robustness for the visual blocks (chart, indicator table, comparison table,
report, ranked results) was added 2026-09-23 alongside the 5.7.1 gap
closure above.

**5.7.2 complete — Interactive controls (2026-09-23).** The three items
listed as remaining are done. **Scenario sliders:** the scenario block now
carries a price-shock slider; dragging it recomputes gross exposure and
total P&L delta client-side using the exact linear formula
`scenario_analysis_tool` itself uses (`scenario_price = base_price * (1 +
shock / 100)`) against the position rows the verified block already
exposes — the delta is provably independent of `entry_price` (it cancels
out algebraically), so no new field or backend call was needed. Recomputed
numbers are visually and textually marked "not verified" and never
overwrite the original verified numbers, which a one-click "Reset to
verified" restores; stop-loss risk is intentionally never recomputed (the
backend model makes it a function of `stop_price`/`entry_price` only,
neither of which moves with a price shock), so it always shows its
original verified value regardless of slider position. **Chart/table
expanders:** evidence (capped at 8), the options chain (capped at 14
contracts), and the mini chart (compact by default) now have a "Show
all"/"Expand" toggle that reveals more of the *same already-delivered*
bounded payload — no new tool call, no new evidence-integrity surface. The
expanded chart also adds high/low labels that weren't shown before.
**Action-specific confirmation:** `action_confirmation` blocks now carry a
`detail` field, sourced from `ChatReplyResponse`'s own typed `action_*`
fields (never the model's prose) via a new `_action_step_detail` helper,
so a confirmation reads e.g. "create_alert: AAPL price above 200" instead
of a bare "create_alert · completed"; actions with nothing tool-specific
to add (market-data reads, `save_to_journal`/`export_report`, which
already have their own richer blocks) fall back to the original generic
line.

Focused verification: 9 new backend tests (`_action_step_detail` plus its
`build_response_blocks` passthrough) and 6 new ChatPanel tests (action
detail, scenario-slider preview/reset math, evidence/options-chain
expand/collapse, chart expand/collapse), plus the full suite and a
production build (2998 backend / 172 frontend tests passing).

**5.7.3 complete — Personal preferences (2026-09-23).** The trader's mode
(day trading / swing trading / options / long-term investing), preferred
timeframes, default session, risk-per-trade limit, primary watchlist,
answer detail level, and preferred units are all stored browser-local
(`marketlens.chat.preferences`, same no-server-table convention as
Journal and Risk Dashboard) via `utils/chatPreferences.ts`, and editable
through a new `⚙ Preferences` panel in the Chat header — every field
visible, editable, and one-click resettable; nothing is inferred
silently. Preferences are sent with each turn and used, on the backend,
for exactly one thing: `_tailored_followups` in `response_blocks.py`
appends one extra mode-specific suggestion (e.g. day trading → "Check the
1m/5m trend") to the deterministic `suggested_followups` block. A
dedicated test (`test_preferences_never_touch_calculation_or_evidence_blocks`)
asserts the calculation block is byte-identical with and without
preferences set — the only block that may differ is the follow-up list.

The prompt-personalization closure now adds a bounded, sanitized
`<preferences>` section to model prompts. It may tailor terminology,
timeframe emphasis, comparison defaults, and risk framing, but the prompt
explicitly forbids changing verified values, tool arguments, or evidence
status. Preferences remain browser-local; only the current validated turn
uses them.

Focused verification: 13 new backend tests (`_tailored_followups`,
`ChatPreferences` request validation, and end-to-end forwarding through
both the blocking and streaming endpoints) and 11 new frontend tests
(storage round-trip/corruption-handling, panel edit/persist/reset, and
turn-level forwarding), plus the full suite and a production build (3011
backend / 183 frontend tests passing).

**5.7.4 complete — Answer contract metadata (2026-09-23).** Every typed
block now carries evidence-derived quality metadata for provider, source time,
freshness, session, timeframe, fallback state, entitlement, confidence, and
grounding. Evidence items expose the same entitlement state for the UI, so a
provider limitation is visible without relying on model prose. The metadata is
derived from the server-side `ToolResult` trace; it is never accepted from a
model-generated quality claim.

The focused response-block contract test covers entitlement propagation. The
current checkout verification is 3,021 backend tests passed with 4
environment-dependent skips, 192 frontend tests passed, and a successful
frontend production build.

**5.7.8 complete — Feedback and correction loop (2026-09-23).** Correct /
Incorrect / Not Useful buttons appear under every assistant message, with
an optional category (wrong data, wrong calculation, misunderstood
intent, stale data, poor explanation, unsafe action) and free-text
comment for Incorrect/Not Useful. Unlike preferences, this is a real
server table — feedback is durable, cross-session, and meant to be
reviewed later, not per-browser scratch state — a new `chat_feedback`
table (migration `20260928_chat_feedback`, one row per message, upserted
so a trader changing their mind replaces rather than accumulates) with a
`POST /api/ai/chat/messages/{id}/feedback` endpoint (404 on a missing
message, 400 on a user message — feedback only applies to assistant
replies) and a batch fetch wired into `GET .../messages` so history shows
previously-given ratings without re-offering the buttons. Storage only:
no automatic online model retraining happens from a rating.

The initial slice did not build the plan's "turn approved failures into
regression fixtures"; explicit promotion was added later (see the
5.7.6–5.7.11 completion note below). Promoted fixtures are stored in the
database only — no test or evaluation run consumes them yet (see Known
gaps).

Focused verification: 14 new backend tests (repository upsert/batch-fetch,
endpoint validation, 404/400 paths, feedback correctly attached to only
its own message in a multi-message transcript) and 4 new frontend tests
(immediate Correct submission, Incorrect with category+comment, historical
feedback rendering without re-offering buttons, no feedback UI on user
messages), plus the full suite, a production build, and the migration
verified against the live dev SQLite database (3024 backend / 187
frontend tests passing).

**5.7.6/5.7.7/5.7.9/5.7.10/5.7.11 completion (2026-09-23).** The Symbol
chart now publishes an explicit bounded chart-state snapshot (symbol,
timeframe, selected session, chart type, active indicators, visible range,
selected candle, drawings, and update time) to browser-local storage. Chat
validates and forwards that state to the backend prompt and persisted evidence
block, so “explain what I’m looking at” can use explicit chart context rather
than infer it from prose or a screenshot. Chat navigation now retains the
relevant incoming context for routed Scanner, Backtest, Risk, Journal, Alerts,
Calendar, Historical Signals, Symbol, Options, and System Health pages: filters,
symbols, watchlist selection, selected records, and the originating chart
timeframe/session are consumed where each page has an equivalent control. The
response UI sends a typed regeneration mode and optional timeframe/session
scope; non-refresh modes remain eligible for recent context reuse, while
Refresh explicitly clears the short-lived Chat context and market baseline
caches before rebuilding evidence. Research notebooks are server-backed by a
stable browser client key, retain grouped symbols, block/content types,
stale/material-change flags, original evidence timestamps, and typed blocks,
and keep browser-local storage as an offline fallback.

The answer contract now computes freshness status server-side (`fresh`,
`recent`, `stale`, or `unknown`) from the evidence age and marks old typed
blocks stale at the same 15-minute threshold used by the refresh control.
Evidence cards expose the status, entitlement, and regeneration/reuse context
to assistive technology through live status text. Added responsive controls,
focus-visible outlines, table header scopes, and route-level navigation tests
improve automated accessibility/mobile coverage; the release checklist is
recorded in `docs/Version_5/phase_5_7_manual_qa.md`.

Refresh comparisons now carry a stable evidence fingerprint and explicitly
report a material change when the refreshed evidence payload differs from the
previous answer; timestamp/age-only changes are excluded from that digest.
Incorrect and not-useful feedback can be explicitly promoted to an approved
regression fixture without any automatic model retraining.

Focused verification includes chart-state and notebook storage tests, chart
state prompt coverage, the existing Chat API suite, the full ChatPanel suite,
and a successful production build. Current checkout verification is 3,030
backend tests passed with 4 environment-dependent skips and 195 frontend tests
passed. The focused gap-closure coverage includes
typed regeneration forwarding/cache invalidation, server-derived stale and
material-change evidence, routed navigation context, server-backed notebook
round trips, approved fixture promotion, grouped notebook metadata, and
mobile/focus-visible UI behavior.

The audit lists every response block, persistence version, accessibility test,
responsive-layout test, preference location, and migration behavior for older
prose-only messages. Every data-backed block is checked for evidence-derived
confidence/data-quality state. Personalization tests cover day-trading,
swing-trading, options, and long-term-investing modes without changing verified
calculations or source evidence. Chart-state, state-preserving navigation,
feedback classification, regeneration, notebooks, and stale/material-change
refresh each have persistence and UI coverage.

---

## Phase 5.8 — Reliability, evaluation, and release hardening

Reliability foundation implemented (5.8.1–5.8.6):

- `backend/ai/answer_verifier.py` assigns application-owned `ev-N` references
  to read-only tool results and checks numeric claims against bounded,
  server-authored evidence values. It rejects unsupported/altered numbers,
  unknown tickers, wrong units, wrong sessions/timeframes, stale or freshness-
  missing live claims, and replaces blocked prose with explicit uncertainty
  before persistence. The existing server-authored destructive-action
  confirmation and approved-payload replay paths remain unchanged. Typed
  calculator traces now retain sanitized inputs and are independently
  recomputed before an answer is persisted; mismatches are blocked.
- Blocking and streaming Chat persistence both store a versioned
  `verification` response block (`5.8.1`) and expose evidence IDs/values in the
  evidence block. The frontend renders verified, limited, and blocked states
  as accessible status text.
- `backend/ai/evaluations/phase_5_8_cases.json` is the versioned provider-free
  verifier harness (`5.8.0`) with nineteen cases covering calculations/quotes,
  calculation mismatches, altered numbers, units, prose/evidence
  contradictions, sessions, timeframes, unknown tickers, freshness, options,
  comparisons, scanner results, portfolio risk, clarification, adversarial
  prompts, provider failure, and confirmation safety. The runner scores
  correctness, tool choice, provenance, clarification, latency, and safety;
  the current report is 19/19 in every category and is recorded in
  `docs/Version_5/phase_5_8_evaluation.md`.
- `backend/ai/chat_observability.py` adds a bounded, sanitized audit event to
  persisted Chat traces: tool/model counts, durations, provider attempts,
  retry count, cache hits, failure kinds, prompt-size metadata, target class,
  and evidence references. Secret-looking keys and private journal prose are
  redacted or omitted.
- The failure matrix is versioned with a two-attempt parse budget, bounded
  planning/chain limits, and explicit stream fallback semantics. Performance
  targets are defined for calculation-only (500 ms), cached-data (1.5 s),
  database (3 s), and provider-backed (15 s) turns, with per-turn target
  compliance persisted in the observability event.
- Checkout validation after the current Chat hardening: full backend suite
  `3,212 passed, 4 skipped, 30 subtests passed`; full frontend suite `212
  passed` across 43 suites; and `npm run build` completed successfully. The
  four backend skips are environment-dependent Redis/loopback checks, not
  product failures.

Security/privacy review and manual smoke evidence (2026-09-23):

- Security/privacy review passed for the implemented reliability slice. The
  focused observability, verifier, response-block, Chat action, Chat router,
  and AI-manager suites passed (`287 passed`); the tracked source scan found
  no private-key or API-key-shaped values outside tests/docs. Sanitized trace
  arguments redact secret-looking keys and private prose, model/observability
  metadata excludes prompt/reply content, and server-authored confirmation
  remains the only path to a destructive action.
- Live API smoke passed for health, a verified blocking calculation, a
  verified streaming calculation with a final persisted frame, an unavailable
  live quote (blocked rather than guessed), and a public-symbol comparison
  with explicit insufficient-data warnings. The comparison also demonstrated
  the bounded two-attempt planning fallback.
- The live calculation-only cold-start turn took `1,691.636 ms`, exceeding
  its `500 ms` target; this is recorded as a performance observation, not a
  correctness or privacy failure. The provider-backed streaming turn stayed
  within its `15,000 ms` target (`5,512.798 ms`).
- Destructive-action and portfolio-risk live smoke was intentionally not
  executed: one could mutate user data and the other could disclose private
  portfolio state to an external provider. Their server gate and privacy
  behavior remain covered by the focused automated suite and require a
  sanctioned fixture/sandbox for manual release testing.

- Sanctioned private-flow smoke is now complete in
  `backend/tests/ai/test_phase_5_8_private_smoke.py` (`1 passed`). It uses
  synthetic positions and journal entries, patches the price-history provider
  with deterministic fixture bars, makes no model/provider calls, and asserts
  that private markers never appear in tool results, sanitized traces, or
  observability metadata.
- Latency decision: retain the `500 ms` calculation-only target for the hot
  steady-state path. Two additional live measurements were `5,539.949 ms`
  for the cold process turn, `1,035.558 ms` for the first warm turn, and
  `4.291 ms` for the repeated hot turn, which passed the target. Startup and
  first SQLite/cache warm-up are accepted release-test outliers; the release
  benchmark must warm the process before judging this target. No target
  relaxation or correctness change was made.

Phase 5.8 completion and final release gate (2026-09-23):

- No order-execution tool or route is present; destructive tools remain
  backend-confirmation-gated.
- Backend validation: `3,212 passed, 4 skipped, 30 subtests passed`.
  The four skips are environment-dependent Redis/loopback checks.
- Frontend validation: `43` suites and `212` tests passed; `npm run build`
  compiled successfully.
- Provider-free Phase 5.8 evaluation: `40/40` end-to-end cases passed in every
  scored category, including opt-in browser positions/Journal privacy cases.
  The sanctioned synthetic private-flow smoke and the recorded
  public/failure-mode live smoke also passed.
- The hot calculation target decision and its cold/warm startup exception are
  documented above. No target relaxation or correctness change was made.

Phase 5.8 is complete. Any remaining action is the normal reviewed handoff
from `development` to the protected stable branch, not unfinished Phase 5.8
implementation work.

### Post-gate semantic routing hardening (2026-09-23)

The AI Hub now has a high-confidence `SemanticRoute` layer before model
synthesis for common natural-language variants. It exposes the scanner-backed
Watchlist Intelligence aggregation as the typed
`get_watchlist_intelligence` Chat tool, aggregates enabled names across all
active watchlists when scope is omitted, preserves exact scope for explicitly
named lists, warms missing symbols on demand, and formats evidence-backed
results for both blocking and streaming Chat. Common symbol research variants also reuse the canonical
typed-tool path. Exact/paraphrase, tool-contract, ambiguity, and formatting
coverage was added; the full backend suite now passes with `3,212` tests,
`4` environment skips, and `30` subtests.

### Chat gap closure (2026-09-23)

The follow-up reliability pass closed the remaining Chat gaps identified after
the release gate:

- blocking and streaming Chat now share one deterministic intent planner,
  including calculation, scanner, scenario, anomaly, signal, assumption,
  historical, and research-tool routes;
- prompt-provided symbol and market context is represented as bounded,
  server-owned verifier evidence, with qualitative labels and symbol-scoped
  numeric matching;
- Chat propagates timeframe/session metadata to tool traces, and the registry
  no longer treats tool execution time as provider data freshness;
- common overview, market, indicator, and portfolio phrasing routes to typed
  tools, while finance acronyms no longer trigger unsupported-ticker failures;
- interrupted streams preserve partial text with an explicit retry message,
  and the frontend renders user-readable verification issues and unknown typed
  blocks safely.

Focused Chat/verifier coverage and the full backend suite pass; frontend Chat
tests and the production build also pass. The four backend skips remain
environment-dependent Redis/loopback checks.

This is hardening of the completed release gate, not a claim that every
possible natural-language expression is deterministic. Unsupported or
open-ended questions still use the bounded model planner, and any new intent
must add a canonical route, a verified tool contract, and evidence tests.

The follow-up scope-continuity gap is also closed. Bounded planner state now
persists the server-resolved named or aggregate watchlist scope and concern,
so a timeframe follow-up reuses the prior scope without silently broadening it
or running unrelated baseline/model work. Unsupported weekly watchlist
requests return a fast explanation naming the preserved scope. The focused
semantic-router/Chat/tool/verifier regression slice passes with `163` tests;
browser verification confirmed the aggregate `All active watchlists` scope is
retained across the follow-up.

Weak-name responses now also use a bounded relative-weakness fallback when
coverage is complete but no name has an outright bearish daily move or
deterioration ranking. The response labels those names as relative weakest
scanner scores and explicitly says when no name is clearly weak.
When cache snapshots omit composite scores but retain trend signals, the
fallback ranks the signed multi-timeframe direction and does not render a
misleading zero composite score.

---

## Chat review fixes (2026-09-23)

A code review of the Chat path against this audit found bugs behind
several "complete" claims. Fixed, each with a regression test:

1. **Unanswered destructive confirmations expired after one turn.**
   `pending_confirmation` used to live until it was confirmed. So after
   "delete my Tech watchlist" → "never mind", any later message starting
   with "ok", "yes" or "sure" that the model answered with `action="none"`
   executed the old deletion (reproduced with "ok thanks"). A carried-over
   confirmation is now cleared at the end of any turn that did not answer
   it (`_expire_carried_confirmation`).
2. **Verifier false blocks.** A bare all-caps word is now treated as a ticker
   only when it is in Chat's own resolver universe (`_known_symbols`), so
   EPS, NYSE, NASDAQ, and CEO no longer block answers. Symbols present in
   successful evidence and in the market baseline are allowed. Phrasal
   "follow up", "set up", "up to", "break down", and "stop loss" are no
   longer read as price-direction claims. The label check no longer reports
   a contradiction when there are no directional values.
3. **Verifier false pass.** A number the user typed no longer verifies a
   claim that server evidence contradicts ("is AAPL at 300?" → "AAPL is at
   $300" is now blocked when the AAPL quote says otherwise). Echoing the
   user's own plan inputs ("your stop at $212") is still allowed.
4. **Material-change false positives.** The evidence fingerprint is compared
   only on regeneration turns; a new question about a different symbol no
   longer marks every block (and the notebook item) as materially changed.
5. **Stream double execution.** Any failure after the `meta` frame now still
   persists one assistant message. The SSE `error` frame carries `started`,
   and the client reloads the conversation instead of resending through the
   blocking endpoint (which used to duplicate the user message and re-run
   non-confirmed actions such as `create_alert`).
6. **Unverified streamed text is labelled.** Deltas arrive before server-side
   verification; the bubble is now marked "Unverified draft" until the
   authoritative final message replaces it.
7. **Market-tool exceptions.** Provider/engine exceptions that are not
   `ValueError`/`TypeError` (for example `InsufficientDataError`,
   `ProviderDataError`) now degrade to a failed tool result instead of
   escaping `_run_action`.
8. **Local notebooks kept on server merge.** Notebooks created while the
   server was unreachable, and items whose server save failed, used to be
   overwritten by the next server load. They are now kept.
9. **Private portfolio scope precedence and stale-data explanations.** Holding
   and position ownership is resolved before generic watchlist/change routes,
   so questions such as "which of my holdings are weakest" and "what changed
   in my portfolio" stay on `get_risk_dashboard` and cannot broaden to all
   active watchlists. The resulting unavailable states identify the missing
   browser-local/server snapshot, while stale relative-strength, pre-market,
   and multi-timeframe questions receive query-specific explanations instead
   of an unsupported generic answer. The focused semantic-router/Chat/
   verifier regression slice passes with 76 tests, and a fresh browser
   submission confirmed the holdings query persisted the private-scope route.

10. **Metric routing and transcript formatting.** Daily language such as
    "change today", "move today", "performance today", and "down today" now
    routes to a previous-close comparison instead of a generic trend or
    calculation fallback. Weekly/monthly return language and explicit windows
    such as "20-day SMA" preserve a bounded period in the typed indicator
    request. Benchmark-relative questions preserve QQQ (or another named
    benchmark) and the exact watchlist scope. Indicator, change, and remaining
    read-only tool results are rendered as concise, freshness-aware prose
    rather than raw provider JSON; browser-local scenario results continue to
    use the aggregate privacy formatter. Semantic market routes run before the
    broad calculation hint, so valid questions containing "return" or "change"
    are not misclassified as arithmetic requests. The focused Chat, verifier,
    observability, private-flow, scanner, and response-block regression slice
    passes with 158 tests, and live local smoke confirmed readable daily-change
    and weekly-return responses with stale-data warnings.

11. **User-facing prose formatting.** Successful calculator and generic
    market-tool replies now lead with trader-readable language rather than
    internal action names, raw JSON, or `key=value` fields. Prices, position
    risk, P&L, options structures, and freshness are formatted explicitly;
    break-even P&L, zero changes, and credit spreads have dedicated regression
    coverage. Typed cards and tool pills continue to render structured UI, not
    raw payloads. Provider-error text is intentionally tracked below as a
    separate remaining sanitization gap.

Verification: `backend/tests/ai` plus `backend/tests/api/test_chat_router.py`
(821 passed) and the frontend ChatPanel/utils suites (72 passed) with a
clean `tsc --noEmit`.

## Known gaps and plan deviations (2026-09-23 review)

These are not fixed. They are recorded so the scorecard is not read as
covering them.

- **Chat provider-error formatting.** The successful-response formatter does
  not yet normalize every `ToolResult.error` before it is interpolated into a
  user-facing unavailable message. A provider may therefore still expose a
  raw error payload on failure; this requires a dedicated error-sanitization
  pass and is not claimed as complete by the prose-format work above.

- **5.3.5 concurrency.** Per-symbol reads inside comparisons and portfolio
  risk now run in parallel, but chained steps still run one after another:
  each continuation is a model decision that depends on what already ran.
- **5.2.4 saved scans.** Scanner presets are browser-local and Chat does not
  send them by default. With the saved-presets opt-in enabled,
  `get_saved_scans` receives a bounded snapshot and a named preset can be
  explicitly reused by the scanner route. Without opt-in it honestly reports
  them unavailable.
- **5.7.8 fixtures.** Promoted fixtures now export into
  end-to-end cases, but each draft needs a maintainer to script its
  evidence and expectations before it runs; nothing is automatic.
- **5.8.3 evaluation coverage.** The end-to-end suite has 40 cases. Deterministic
  tool routes are covered well; open-ended model-planned answers are covered
  only through scripted model replies, so real model tool-choice quality is
  not measured. The verifier suite's tool-choice and clarification columns
  remain structural checks only.
- **5.8.1 citations.** Numeric claims are matched against any evidence value
  in scope; the model is not required to cite `ev-N` references. Turns with
  an action step or a trusted server reply skip prose verification (their
  text is server-formatted).
- **5.7.5 / 5.8.7 manual checks.** The device/screen-reader pass in
  `phase_5_7_manual_qa.md` has not been run. The destructive-action and
  portfolio flows were covered by automated and synthetic tests, not live
  smoke.
- **Audit tables.** Written in [phase_audit_v5_tables.md](phase_audit_v5_tables.md):
  - 5.1 formulas and golden tests;
  - 5.2 per-tool source, cache, freshness, fallback, and tests;
  - 5.4 test counts;
  - 5.7 per-block persistence, quality, and accessibility;
  - the plan's 12-scenario matrix (11 covered, 1 partial: #5 from Chat; the
    opt-in/privacy path is covered, but live UI smoke and a full Chat shock
    calculation remain open).

  Freshness findings from the tables are fixed:
  - composite tools report their oldest source;
  - tape reports its last trade time;
  - snapshot and computed tools no longer claim call time;
  - naive timestamps are read as New York time.

  Adding coverage also found and fixed four behavior bugs:
  - `historical_similarity` outcomes overlapped the current window;
  - "previous close" in `what_changed` meant the previous bar;
  - comparisons mixed observation times without saying so;
  - options results lacked a delayed label.

  Still open:
  - live UI smoke for opt-in private flows and a full Chat portfolio-shock
    calculation remain open; the sanctioned provider-free harness covers the
    routing and privacy boundary;
  - three analysis tools have only one or two behavioral cases.
- **Plan open questions.** Q4 (blocks stored as versioned JSON in
  `chat_messages.response_blocks`) and Q5 (preferences stay browser-local)
  are decided by the implementation; Q1–Q3 remain open.

## End-to-end Chat evaluation and tool timeouts (2026-09-23)

- **Evaluation suite.** `backend/ai/evaluations/chat_runner.py` runs 40
  versioned conversations (`phase_5_8_chat_cases.json`) through the real
  blocking/streaming Chat path, with only the model, tools, context, and
  database scripted. All 40 pass; categories are counted only where a case
  sets an expectation. The suite includes opt-in browser positions/Journal
  routing and checks that private markers do not persist. See
  `phase_5_8_evaluation.md`.
- **Regression fixtures** export via
  `python -m backend.ai.evaluations.export_fixtures` into reviewable case
  drafts that run with the suite after review (closes the 5.7.8
  "nothing consumes fixtures" gap, with a manual review step).
- **Gaps the harness found, now fixed:**
  - New calculator operation `position_risk`: per-share risk, total risk,
    position value, and optional portfolio-risk percent and reward/risk as
    separate outputs, as 5.1.2 requires.
  - Labelled-input parsing, so "Buy 200 AAPL at $220, stop $212" is
    calculated deterministically.
  - "Use the same stop but 100 shares" replaces only the named inputs of
    the remembered calculation.
- **Tool timeout (5.1.4 / 5.8.5).** Read-only and calculation tools now run
  under a hard deadline (`AI_CHAT_TOOL_TIMEOUT_SECONDS`, default 20 s, or
  `ToolSpec.timeout_ms`). An overrun returns `failure_kind: "timeout"`.
  Mutating tools get no deadline. The failure matrix (`5.8.1`) adds
  tool-timeout and model-outage rows linked to their end-to-end cases.

## Per-turn budgets (2026-09-23)

Plan 5.3.1's budgets are now separate limits, and each one reports a
stopped planning step with its own reason when it is reached:

| Setting | Default | Counts |
| --- | ---: | --- |
| `AI_CHAT_MAX_TOOL_CALLS` | 5 | actions executed in the turn, the first included (`tool_budget_exhausted`) |
| `AI_CHAT_MAX_PLANNING_CALLS` | 2 | follow-up model calls choosing the next step; the first reply and parse retries are not counted (`planning_budget_exhausted`) |
| `AI_CHAT_MAX_TURN_TOKENS` | 60,000 | estimated tokens (chars / 4 of prompt, system, and reply) across every model call (`token_budget_exhausted`) |
| `AI_CHAT_MAX_TURN_SECONDS` | 30 | wall-clock time before another continuation starts (`time_budget_exhausted`) |

Every model call now records `estimated_tokens`. A continuation is not
started when its estimated cost would exceed the remaining token budget. A
single call's prompt is also sized so the prompt, system text, and reply
fit inside the turn budget. `AI_CHAT_MAX_CHAIN_STEPS` is deprecated; if it
is still set, it caps tool calls so an existing `.env` keeps its behavior
until the line is removed. The end-to-end evaluation pins these defaults
(so results do not depend on a machine's `.env`) and adds
`planning_budget_stops_long_chain`.

## Server-side regeneration and broader evaluation (2026-09-23)

- **Regeneration modes (5.7.9).** The frontend now resends the original
  question with a typed `regeneration_mode` (`again`, `more_detail`,
  `simpler`, `bull_case`, `bear_case`, `calculations_only`, `sources_only`,
  `refresh`, `rescope`) and an optional timeframe/session scope. No
  instruction text enters the user message, the transcript, or intent
  routing. The backend adds a fixed, server-authored `<regeneration>` prompt
  section per mode (`backend/ai/prompt.py::REGENERATION_INSTRUCTIONS`) to the
  synthesis and continuation prompts. It tells the model never to change
  verified numbers, tool arguments, or evidence status.
- **Timeframe/session rescope.** "Apply timeframe/session" sends `rescope`.
  The scope replaces the timeframe/session argument of any tool whose input
  accepts it, for that turn only; remembered timeframe/session memory is not
  changed. Every regeneration mode (including `again`) is compared against
  the previous answer's evidence for material change.
- **Evaluation.** The end-to-end suite (`5.8.4`) has 32 cases, adding
  regeneration, rescope, session/timeframe memory, why-did-it-move/news
  routing and clarification, and journal review/confirmed save. Temporarily
  disabling the server-side instruction or the tool rescope makes the new
  cases fail.

## Per-block quality, dates, memory, user-data tools, parallel reads (2026-09-23)

- **Per-block quality (5.7.1 / 5.7.4).** Calculation and visual blocks
  (chart, indicator table, options chain, risk/scenario/session cards,
  historical outcomes, comparison/ranked results, report, journal save) now
  carry the quality of the one tool result that produced them. Answer-level
  blocks (prose, evidence, verification, warnings, actions) carry the
  turn's weakest input: a fallback source first, then the oldest data age.
  Previously they used whichever tool ran last. A failed model call no
  longer marks the answer partial.
- **Relative dates (5.1.3).** `tool_registry.resolve_relative_date` resolves
  "today", "yesterday", "N days ago", "last week", and "last/on/since
  <weekday>" to New York calendar days. A date named in the current message
  bounds `market_event_timeline` and `get_signal_history` (start/end) and
  turns "what changed since <day>" into a comparison with that day's close.
  "today" and "yesterday" keep `what_changed`'s own references.
- **Structured memory (5.3.3):**
  - `date_range` is remembered.
  - `watchlist` is set when a message names a real watchlist, and follows
    create/add/remove/delete-watchlist actions.
  - `DELETE /api/ai/chat/sessions/{id}/memory` (a "Reset memory" button in
    Chat) clears all structured memory, including any pending confirmation,
    and keeps the messages.
- **User-data tools (5.2.4):**
  - `get_signal_history` reads recorded engine signals, trend-state
    transitions, and forward outcomes from the database.
  - `get_risk_dashboard`, `get_trade_journal`, and `get_saved_scans` accept
    bounded, relevant-question browser snapshots only when their category is
    opted in; otherwise they report browser-local data as unavailable.
  - Saved Scanner presets can be explicitly reused by name, and persisted
    Chat answers keep aggregate/sanitized data rather than raw browser rows.
  - All have deterministic Chat routes.
- **Parallel reads (5.3.5).** `compare_symbols` and `assess_portfolio_risk`
  fetch their per-symbol bars concurrently (at most 4 at a time), still once
  per symbol.
- **Evaluation.** The end-to-end suite (`5.8.6`) has 40 cases, all passing,
  including opt-in browser-data routing and persisted-message privacy checks.
