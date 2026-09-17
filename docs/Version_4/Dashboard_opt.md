Reviewed the Dashboard and dependencies read-only; no files were changed.

Highest-impact optimizations
Reduce the initial request fan-out — High
Dashboard can issue up to 15 GETs before child cards: regime, sector, 10 trend timeframes, confluence, strategy, and market context (frontend/src/pages/Dashboard.tsx:55-132, frontend/src/services/api.ts:1237-1242).
Add a dashboard aggregate/batch endpoint, or derive the trend cards from confluence.timeframe_signals when the selected preset is sufficient.
The strategy endpoint also recomputes regime, trend, and MTF signals (backend/api/strategy/router.py:54-69), duplicating work already requested by the page.
Avoid duplicate Top Movers scans — High
The page requests bullish and bearish movers separately (frontend/src/components/TopMoversCard.tsx:100-103).
Each backend request scans the watchlist independently (backend/api/scanner/router.py:367-416).
A combined endpoint that scans once and returns both rankings would reduce backend CPU and latency.
Prevent stale responses after symbol or preset changes — High
There is no AbortController, request ID, or stale-response guard (frontend/src/pages/Dashboard.tsx:125-164).
A slow request for the previous symbol/preset can overwrite newer data.
Cancel obsolete GETs and ignore responses that no longer match the current request generation.
Fix duplicate symbol-fetch behavior — High
SymbolInput calls onChange and then onSubmit (frontend/src/components/SymbolInput.tsx:23-30).
Because parent state updates asynchronously, submitting a new symbol can fetch the old symbol and then fetch the new symbol again through the effect (frontend/src/pages/Dashboard.tsx:134-136).
Let the parent effect handle the fetch, or pass the normalized symbol directly to the submit action.
Unify refresh behavior — High
The 30-second auto-refresh only refreshes the five core Dashboard sections (frontend/src/pages/Dashboard.tsx:125-144).
Top Movers, Digest, and Transitions have separate initial/manual refresh logic (frontend/src/components/TopMoversCard.tsx:134-136, DigestCard.tsx:72-74, TransitionsMiniCard.tsx:58-60).
Use a shared refresh event or clearly label the control as “Refresh core analysis.”
Also prevent interval, visibility-change, and manual refreshes from overlapping.
Correct the “Last updated” timestamp — Medium
lastUpdated starts as new Date() and is updated when refresh starts, not when data finishes (frontend/src/pages/Dashboard.tsx:29, 199-202, 125-132).
Initialize it to null and update it after requests settle.
globalError is declared but never populated (Dashboard.tsx:30, 205); remove it or wire it to aggregate errors.
Use a real skeleton and stable initial layout — Medium
DashboardSkeleton.tsx exists but is unused, and card-loading-skeleton has no CSS definition.
Loading cards currently show “No data available” (frontend/src/pages/Dashboard.tsx:207-237).
The existing skeleton is also outdated: it shows four trend cards and omits the Digest card, while the real page shows ten trends and four bottom cards (frontend/src/components/skeletons/DashboardSkeleton.tsx:4-9).
Make API requests resilient — Medium
api.fetch has no timeout, abort signal, retry policy, or explicit conditional-cache handling (frontend/src/services/api.ts:1114-1128).
Add request timeouts and cancellation.
Consider stale-while-revalidate or explicit ETag/If-None-Match handling so a slow refresh does not leave cards stuck in loading state.
Avoid refetching symbol-independent data — Medium
Market context does not depend on the selected symbol, but it is refetched whenever the symbol changes because it is part of fetchAll (frontend/src/pages/Dashboard.tsx:112-132).
Fetch it separately and cache it at an appropriate interval.
Split regime and sector loading — Medium
Regime and sector are bundled with Promise.all (frontend/src/pages/Dashboard.tsx:56-70).
A sector failure currently causes the entire Regime card to show an error even if regime data succeeded.
Store and render them independently for better partial-success behavior.
Measure and selectively code-split the main bundle — Medium
Dashboard imports all cards statically (frontend/src/pages/Dashboard.tsx:3-15), and the app intentionally keeps Dashboard in the main bundle (frontend/src/App.tsx:7-17).
Run npm run build:analyze (frontend/package.json:18) before changing this.
If the bundle is large, lazy-load below-the-fold cards such as Digest, NL Search, or Top Movers while keeping the primary analysis cards eager.
Consolidate repeated state management — Medium
Dashboard manually maintains five data/loading/error state groups (frontend/src/pages/Dashboard.tsx:32-54).
useDashboardData.ts already provides this pattern but is unused (frontend/src/hooks/useDashboardData.ts:8-34).
Adopt it or replace the manual states with a typed reducer/store to reduce duplication and race-condition risk.
Lower-priority improvements
Improve narrow-screen behavior — Medium
grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)) can overflow very narrow mobile screens (frontend/src/styles/App.css:366-370).
Use minmax(min(100%, 320px), 1fr) or a dedicated mobile breakpoint.
Improve keyboard/accessibility semantics — Medium
NL Search result rows are clickable but not keyboard-operable (frontend/src/components/NLSearchBar.tsx:93, 294-300).
The Transitions symbol has role="button" but no tabIndex or key handler (frontend/src/components/TransitionsMiniCard.tsx:69-75).
Top Movers uses a clickable div without an ARIA label (frontend/src/components/TopMoversCard.tsx:52-63).
Add labels and keyboard handling, or use native buttons/links.
Remove avoidable per-render work — Low
Confluence sorts timeframe entries on every render (frontend/src/components/ConfluenceCard.tsx:66-69).
TrendCard recreates its timeframe-label map on every render (frontend/src/components/TrendCard.tsx:35-46).
Move constants outside components or use useMemo; the performance gain is small but the code becomes cleaner.
Clean up duplicated CSS — Low
.card, .empty-state, .dashboard-header, and .summary-grid are defined more than once with overlapping rules (frontend/src/styles/App.css:209-226, 539-565, 581-593, 1905-1917).
Consolidating them will reduce surprises and maintenance cost.
Harden child-card data handling — Low/Medium
Digest assumes mover arrays and numeric RSI values are always present (frontend/src/components/DigestCard.tsx:162-204).
Transitions uses array indexes as React keys (frontend/src/components/TransitionsMiniCard.tsx:102-120).
Add payload guards and stable keys to prevent intermittent rendering failures.
Existing patterns worth preserving are progressive card rendering, parallel independent requests, memoized analysis cards, visibility-change refresh, and aborting stale NL searches. The current Dashboard does not render CandlestickChart or MultiTimeframeChartGrid, so chart-specific optimizations are not part of the current page’s runtime cost.