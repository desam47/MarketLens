export const meta = {
  name: 'review-live-scanner',
  description: 'Adversarially review the Live Scanner feature (backend REST, ranking engine, scoring/signals, WebSocket live path, frontend rendering) against the live backend',
  phases: [
    { title: 'Review' },
    { title: 'Verify' },
  ],
}

const LIVE = 'The backend is LIVE at http://127.0.0.1:5001. Project root is /Users/dips/projects/MarketLens. Use bash + python3 with urllib to probe it, and Read/grep for code. The DB is SQLite at the project root; Redis may be running.'

const KNOWN = [
  'Lead A (believed CONFIRMED): POST /api/scanner/filter, /rankings, /top-movers ignore the symbols= scope param for scoping. router.py reads the module-global list(market_scanner.scan_results.values()) at ~lines 240, 264, 317, 358, so ranking/filtering runs over EVERY symbol ever scanned, not the requested scope. The symbols param only triggers a pre-scan. Live pool is 10 symbols: SPY IWM QQQ VIXY CYN CTNT MSFT AAPL NVDA DVLT.',
  'Lead A-ext (believed CONFIRMED): _build_filter(filters, match) in router.py (~line 161) returns AndFilter([DailyBullish(min_confidence=-1.0)]) when filters is empty, with a docstring claiming this is match-all. It is NOT match-all. Live probe: POST /api/scanner/filter with body {"filters":[],"match":"AND"} returned 2 rows (AAPL, MSFT); with {"filters":[{"type":"true","params":{}}],"match":"AND"} it returned 10 rows. Same for /rankings: total_eligible=2 vs total_eligible=10. Root cause candidate: TimeframeDirection.matches still requires direction=="uptrend" regardless of how negative min_confidence is.',
  'Lead A-ext2 (believed CONFIRMED, highest impact): frontend/src/components/NamedRankingsPanel.tsx line ~136 ALWAYS calls api.getRankings({filters: [], match: "AND"}, n, symbols). So the Live Scanner Rankings panel is permanently gated to the ~2 symbols that happen to be daily-uptrend, while displaying itself as a full-scope ranking.',
  'Lead N (believed CONFIRMED): backend/scanner/ranking.py _build_strongest_bullish sorts descending by _directional_score and _build_strongest_bearish sorts ASCENDING by the SAME metric, over the same pool. Live: GET /api/scanner/top-movers?direction=bullish&limit=10 returns CYN CTNT NVDA QQQ SPY AAPL IWM DVLT VIXY MSFT and direction=bearish returns the exact reverse. All composite totals are positive (18.83-38.05).',
  'Lead N-ext (believed CONFIRMED): TopMoversCard.tsx isBullish() classifies by bull/bear signal-name sets; HIGH_VOLUME (emitted on every row) is in neither set, so isBullish degenerates to total_score > 0. Live simulation: the Bullish card renders only 6 rows and drops DVLT (the HIGHEST total_score, 38.05); the Bearish card renders 4 rows, all with POSITIVE scores (VIXY 22.20, DVLT 38.05, CTNT 25.95, CYN 20.08).',
  'Lead K (believed CONFIRMED): ScanResult.scores holds signed factors but the unsigned magnitude factors dominate (live SPY: volume 100.0, trend_strength 13.91, adx 11.59, volatility 7.27, momentum 0.12, macd 0.24, rsi 0.0 -> total 19.02). Every observed total is positive, so the <=-30 bearish styling band in ScannerPage scoreClass/scoreColor and the bearish stat counter are unreachable.',
  'Lead O (believed CONFIRMED): ScannerPage TREND_TFS lists only 6 timeframes (1m 5m 15m 1h 4h 1d) and the scanner only populates those 6 in trend_signals, but FilterBuilder offers 30m and 1w timeframe options that can therefore never match.',
  'Lead F (believed CONFIRMED by code): useScannerStream.ts - the [symbols] effect cleanup unsubscribes ALL previous symbols without disconnecting, then the body subscribes only symbols NOT in prevSymbols. Symbols present in both old and new lists get unsubscribed by cleanup and skipped by the body -> permanent loss of live updates (unsubscribe deletes from the sub Set, so reconnects do not restore). Reachable: GET /api/watchlists/ shows two watchlists (id 2 Market Context 4 symbols, id 1 Default 6 symbols), so switching watchlists with shared symbols triggers it.',
  'Lead E (believed LOW / dead code, not a leak): useScannerStream.ts line ~118 calls sub.onStatus(setConnectionStatus) and discards the returned unsubscribe. statusListeners is a Set, onStatus replays current status itself, setConnectionStatus is a stable identity -> redundant dead code with a misleading comment, plus useRef(api.createScannerSubscriber()) allocates a throwaway subscriber every render.',
  'Lead H (believed CONFIRMED by code): TopMoversCard scoreBadge uses #10b981 / #dc2626 for |score|>50, inconsistent with the app trend convention #22c55e / #ef4444 (doc 4.1.12).',
  'Lead I (believed CONFIRMED by code): ScannerPage renders a "live stream paused" notice when a filter is applied (line ~414) but nothing actually pauses the WebSocket or the live result updates.',
].join('\n- ')

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string' },
          claim: { type: 'string' },
          evidence: { type: 'string' },
          live_verified: { type: 'boolean' },
        },
        required: ['id', 'title', 'file', 'severity', 'claim', 'evidence', 'live_verified'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    corrected_severity: { type: 'string' },
    corrected_claim: { type: 'string' },
  },
  required: ['refuted', 'reason', 'corrected_severity'],
}

const DIMENSIONS = [
  {
    key: 'backend-rest',
    prompt: 'You are reviewing the backend REST surface of the MarketLens Live Scanner. Read backend/api/scanner/router.py in FULL, plus backend/api/ttl_cache.py. ' + LIVE + '\n\nKnown leads to confirm or refute:\n- ' + KNOWN + '\n\nGo BEYOND the known leads: look for unhandled exceptions on malformed bodies, pagination/top_n clamping, symbols param handling, missing response fields, the ttl_cached scan cache interacting badly with the global scan_results accumulation, Pydantic model vs actual dict shape mismatches, and any endpoint that mutates global state as a side effect of a GET. Actually probe the live backend with python3/urllib for every claim you make. Return only findings you can back with either a live probe result or an exact code citation. Prefer few high-confidence findings over many speculative ones. Use ids like backend-rest-1.',
  },
  {
    key: 'ranking-engine',
    prompt: 'You are reviewing the ranking engine of the MarketLens Live Scanner. Read backend/scanner/ranking.py in FULL (CATEGORIES, _DIRECTIONAL_WEIGHTS, _directional_score, _build_strongest_bullish, _build_strongest_bearish, all other _build_* builders, rank(), last_result). ' + LIVE + '\n\nKnown leads to confirm or refute:\n- ' + KNOWN + '\n\nGo BEYOND: check every one of the 7 ranking categories for whether its build function can actually produce the semantic its description claims; check biggest_improvement / biggest_deterioration for whether they use a direction-less metric; check rank vs total_eligible vs the description of each category; check the mutable module-global default_ranking_engine.last_result for cross-request contamination or thread-safety. Probe the live backend (GET /api/scanner/rankings/categories, POST /api/scanner/rankings) for every claim. Use ids like ranking-1.',
  },
  {
    key: 'scoring-signals',
    prompt: 'You are reviewing scoring and signal generation in the MarketLens Live Scanner. Read backend/scanner/scanner.py in FULL, focusing on calculate_total_score / calculate_signed_total_score and _generate_signals. ' + LIVE + '\n\nKnown leads to confirm or refute:\n- ' + KNOWN + '\n\nGo BEYOND: enumerate which signal names the engine can actually emit and compare against what the frontend expects; check the confidence threshold convention (scanner.py appears to use > 0.6 while ranking.py and filters.py use >= 0.5) for whether it silently drops signals the UI documents; check whether calculate_total_score divides by abs-summed weights or by something else and whether the docstring matches the code; check what happens with a single-factor or all-zero-factor result. Probe the live backend (GET /api/scanner/SPY, GET /api/scanner/filter-types) for every claim. Use ids like scoring-1.',
  },
  {
    key: 'websocket-live',
    prompt: 'You are reviewing the WebSocket / live-update path of the MarketLens Live Scanner. Read backend/api/scanner/ws_router.py in FULL and frontend/src/hooks/useScannerStream.ts in FULL and the ScannerSubscriber class in frontend/src/services/api.ts (roughly lines 720-890). ' + LIVE + '\n\nKnown leads to confirm or refute:\n- ' + KNOWN + '\n\nGo BEYOND: check for unsynchronized counter mutation without the lock in ws_router, check the ScannerDispatcher quote handler for whether it re-scans ALL subscribed symbols on every quote (cost), check backoff/ping/reconnect behavior for exhaustion or thundering herd, check whether unsubscribe/subscribe races can corrupt the subscription Set, check whether the client can permanently lose updates (the F lead) and what exactly the trigger sequence is. You may probe the WS with a python websocket client if a library is available, otherwise reason from code and probe the REST surface. Use ids like ws-1.',
  },
  {
    key: 'frontend-render',
    prompt: 'You are reviewing the frontend rendering of the MarketLens Live Scanner. Read frontend/src/pages/ScannerPage.tsx in FULL, frontend/src/components/TopMoversCard.tsx in FULL, frontend/src/components/NamedRankingsPanel.tsx in FULL, frontend/src/components/HistoricalSignalCard.tsx, frontend/src/components/FilterBuilder.tsx, and frontend/src/components/skeletons/ScannerTableSkeleton.tsx. ' + LIVE + '\n\nKnown leads to confirm or refute:\n- ' + KNOWN + '\n\nGo BEYOND: check every place the UI displays a score, a direction, a count, or a color against what the live API actually returns; check the timeframe option lists against the timeframes the backend actually populates; check whether any panel claims a scope (watchlist, top-N, direction) that the data does not honor; check empty/loading/error states for a state where the UI shows something misleading rather than an error. You cannot render React but you CAN fetch the live payloads the components consume and simulate their classification logic in python3. Use ids like ui-1.',
  },
]

phase('Review')
const reviewed = await pipeline(
  DIMENSIONS,
  d => agent(d.prompt, { label: 'review:' + d.key, phase: 'Review', schema: FINDINGS_SCHEMA, effort: 'high' }),
  (res, d) => {
    const fs = (res && res.findings ? res.findings : []).slice(0, 3)
    log(d.key + ': ' + fs.length + ' findings -> verifying')
    return parallel(fs.map(f => () =>
      agent(
        'You are an adversarial verifier. Another agent claims the following about the MarketLens Live Scanner. Your job is to REFUTE it. Default to refuted=true unless the claim survives your own independent check.\n\nCLAIM: ' + f.title + '\nFILE: ' + f.file + (f.line ? ':' + f.line : '') + '\nCLAIMED SEVERITY: ' + f.severity + '\nWHAT THEY SAY: ' + f.claim + '\nTHEIR EVIDENCE: ' + f.evidence + '\n\n' + LIVE + '\n\nDo this, do not skip steps: (1) Read the exact code region yourself and confirm the quoted behavior actually exists as described. (2) If the claim asserts a runtime/live behavior, reproduce it yourself against the live backend and paste the actual output. (3) Actively hunt for a reason it is NOT a bug: is the behavior intentional and documented? Is the code path unreachable in practice? Is the UI actually protected by a guard the claimant missed? Does the frontend pass parameters that avoid the problem? (4) If the claim is directionally right but overstated or understated, say so in corrected_claim and corrected_severity rather than refuting outright.\n\nReturn refuted=true only if the claim does not hold. Otherwise refuted=false and give the single strongest piece of evidence you produced.',
        { label: 'verify:' + f.id, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'high' }
      ).then(v => ({ finding: f, dimension: d.key, verdict: v }))
    ))
  }
)

const all = reviewed.flat().filter(Boolean)
const confirmed = all.filter(x => x.verdict && x.verdict.refuted === false)
const refuted = all.filter(x => x.verdict && x.verdict.refuted === true)

log('verified ' + all.length + ' findings: ' + confirmed.length + ' confirmed, ' + refuted.length + ' refuted')

return {
  confirmed: confirmed.map(x => ({
    dimension: x.dimension,
    title: x.finding.title,
    file: x.finding.file,
    line: x.finding.line,
    claimed_severity: x.finding.severity,
    corrected_severity: x.verdict.corrected_severity,
    claim: x.verdict.corrected_claim || x.finding.claim,
    evidence: x.finding.evidence,
    verifier_reason: x.verdict.reason,
  })),
  refuted: refuted.map(x => ({
    dimension: x.dimension,
    title: x.finding.title,
    reason: x.verdict.reason,
  })),
}
