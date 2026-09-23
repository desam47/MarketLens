# Version 5 Phase Audit

**Last updated:** 2026-09-22 (re-scoped from Charts to Intelligent AI Hub Chat)
**Status:** Active. Planning complete; Phase 5.1, Phase 5.2, and Phase 5.3 complete.
**Scope:** Grounded tool-using Chat, verified calculations, market/user-data retrieval, bounded orchestration, analysis workflows, structured UI, personalization, and reliability evaluation.
**Branch workflow:** Version 5 implementation is developed on `development`; `main` remains the protected stable branch and receives reviewed merges only.

**Current checkpoint (2026-09-22):** Version 4's implemented scope is
merged to `main`. The `development` branch is synchronized with its remote
and is the only branch receiving new Version 5 work. Phase 5.1 is complete.
Phase 5.2 is complete: every tool explicitly named in the plan's
5.2.1–5.2.8 items is implemented, with consistent provenance fields and a
genuinely generated (not hand-copied) application-help route table (see
the Phase 5.2 section's "Post-completion review" for the two items —
multi-provider reconciliation, further contract-test expansion — found to
be blocked on real architectural gaps rather than left undone). Phase 5.3
is now the active delivery gate.

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
| 5.2 | Grounded market-data tools and provenance | ✅ COMPLETE | 23 tools registered, every tool named in 5.2.1–5.2.8 implemented, with consistent freshness/fallback/entitlement fields and a generated (not hand-copied) application-help route table. 8 of 23 tools have live contract tests — traced to be close to the practical ceiling for this codebase (see 2026-09-22 review). Multi-provider reconciliation stays unit-tested infrastructure — no real multi-observation path exists to wire it into without a deliberate architecture change. |
| 5.3 | Bounded orchestration, intent, and memory | ✅ COMPLETE | Bounded chaining, budgets, duplicate suppression/reuse, persistent memory and confirmations, deterministic intent routes, visible step decomposition, reusable workflows, role-specific model routes, and AI-off evidence-only fallback are implemented and tested. |
| 5.4 | Analysis, comparisons, scenarios, and explanations | 🟡 IN PROGRESS | The typed `why_did_it_move` and `what_changed` tools now provide evidence and baseline comparisons with explicit unknowns. Rankings, scenarios, similarity, and sensitivity remain. |
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

Application-help (5.2.7) now carries a `required_state` field per page (e.g.
the Symbol page declares `["symbol"]`, since the frontend carries the
selected symbol as React state rather than a URL parameter — no app
navigation action exists yet to consume it; that is Phase 5.7.7's job, and
this only makes the current informational answer state the requirement
honestly). Its 12-page table remains hardcoded rather than dynamically
derived — a real cross-language source-of-truth split, since the actual
router lives in TypeScript (`frontend/src/utils/appNavigation.ts`) and
Python cannot import it — but auditing it caught a real, live bug: the
"signals" page pointed at `#historical-replay`, a legacy alias the frontend
still *accepts* on incoming links but never *produces* when navigating
(`hashForPage('signals')` returns `#signals`). Fixed, and now guarded by
`test_application_help_routes_match_frontend_canonical_hashes`, which
hardcodes the frontend's 12 canonical page→hash pairs as an explicit
drift trip-wire — it will fail loudly the next time either side adds,
renames, or removes a page without the other being updated. True dynamic
generation (reading the frontend's route table at test/build time, or a
shared JSON manifest both stacks consume) is still not implemented; the
hardcoded-but-guarded state here is a stopgap, not the plan's original
ask.

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
budgets (default three steps), duplicate action signatures stop safely, and
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
Phase 5.4 is the active delivery gate, with the first evidence and comparison
slices implemented.

---

## Phase 5.4 — Analysis, comparisons, scenarios, and explanations

The first `why_did_it_move` and `what_changed` slices are implemented and tested. Audit must include golden numerical fixtures, aligned-timeframe
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
