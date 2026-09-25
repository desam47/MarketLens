# Version 5 Trend Cards Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (TC-01 Trend implementation)
**Status:** Re-prioritized from a trading-desk perspective: what would actually cost a trader money first, not just severity as originally filed. TC-01's Trend API and card contract are implemented and covered by focused tests; Confluence still needs to consume that same contract, so TC-01 remains partial. TC-01 and TC-06 describe one underlying fix — a single server-owned evidence contract — and are tracked together under TC-01. TC-08 remains High because it is a silent scope-mismatch bug, not a workflow nicety.
**Scorecard:** 0 ✅ COMPLETE, 1 ⚠️ PARTIAL, 8 ❌ NOT STARTED, 0 🟡 DEFERRED, 1 folded (TC-06 → TC-01).
**Source:** 2026-09-24 code, API, and live-payload review of the Dashboard's **Trend by Timeframe** cards at `78a1adc`, re-prioritized the same day from a trading-desk perspective and re-verified live against SPY.
**Related:** [Phase audit](phase_audit_v5.md), [AI Analysis fixes](v5_ai_analysis.md), [Historical Signals fixes](v5_historical_signal_bug_fixes.md), [Market Data fixes](v5_market_data_bug_fixes.md)

"Verified" means the behaviour was observed through the local running API during the review (and, for TC-01 and TC-03, observed a second time in this prioritization pass). "Code-read" means it follows from the current implementation but was not separately reproduced. The initial review made no changes; later implementation work is recorded in the fix log.

Status legend (same as the phase audits):

- ✅ **COMPLETE** — fixed and covered by tests
- ⚠️ **PARTIAL** — the harmful behaviour is fixed, but specific gaps remain (listed in the entry)
- ❌ **NOT STARTED** — no change made yet
- 🟡 **DEFERRED** — intentionally postponed

Pre-implementation line numbers refer to the code at `78a1adc`; implementation entries name their current files directly.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| TC-01 | Critical | UI + data truth | Cards cannot prove freshness or validity — stale, warming, and invalid data all look like an ordinary closed bar (includes former TC-06) | Verified | ⚠️ PARTIAL |
| TC-03 | Critical | Methodology | 1m, 2m, and 3m Strength is a fixed default rather than a measurement | Verified | ❌ NOT STARTED |
| TC-08 | High | Workflow + data truth | The all-timeframe grid and the selected Confluence preset do not share one scope | Code-read | ❌ NOT STARTED |
| TC-05 | High | Methodology | Confidence and score are not comparable across short and long timeframes | Code-read | ❌ NOT STARTED |
| TC-02 | High | Provenance | Live intraday Trend cards lose their market-data provider | Verified | ❌ NOT STARTED |
| TC-04 | High | Methodology | Per-timeframe `Very Strong` can never be emitted, and may be silently diluting `Strong` | Code-read | ❌ NOT STARTED |
| TC-07 | Medium | UI + contract | Raw score and classification are available but hidden from Trend Cards | Code-read | ❌ NOT STARTED |
| TC-09 | Medium | Workflow | Cards lack change history, indicator attribution, key-level context, and chart handoff | Code-read | ❌ NOT STARTED |
| TC-10 | Medium | Tests | No direct TrendCard regressions protect trader-facing truth states | Code-read | ❌ NOT STARTED |
| ~~TC-06~~ | — | — | ~~Cards cannot explain warming, invalid, delayed, or unavailable evidence~~ — folded into TC-01, same evidence contract | Verified | folded |

**Trading-desk order — what would actually cost a trader money first, not filing order:**

1. **Batch 1 — trader-facing truth.** TC-01 (the merged freshness + validity contract — this one fix also resolves former TC-06), then TC-03, then TC-04. TC-10's fixtures are written *inside* this batch, one per finding, as each is built — not deferred to a trailing batch. A truth-state fix with no regression test protecting it the same day is not actually fixed; the original plan's own TC-10 text said "before Batch 1" while its batch placement said "after Batch 3." This corrects that contradiction.
2. **Batch 2 — evidence a screener or alert can trust, not just a person reading the dashboard.** TC-08 first: the natural reading of ten cards sitting beside a Confluence badge is that the badge summarizes the cards, and it doesn't say otherwise when it isn't true. Then TC-05: uncalibrated cross-timeframe comparison is the more dangerous version of the same "which evidence actually counted" problem, and is the one most likely to silently corrupt an Alert or Scanner rule built on these numbers, not just a glance at the screen. Then TC-02.
3. **Batch 3 — depth and professional workflow.** TC-07, TC-09.

---

## Critical

### TC-01 — Cards cannot prove freshness or validity: stale, warming, and invalid data all look like an ordinary closed bar

**Status:** ⚠️ PARTIAL — the Trend API/card path now has the evidence contract; Confluence adoption remains.
**Where:** `TrendCard` (`frontend/src/components/TrendCard.tsx:60`), the Trend payload builder (`backend/api/trend/router.py:37` and `:42`), and the richer Multi-Timeframe snapshot's bar-count / warm-up / validity fields, which already exist but are not exposed to this card.

This entry merges the original TC-01 ("stale data presented as an ordinary closed bar") and TC-06 ("cards cannot explain warming, invalid, delayed, or unavailable evidence"). Both need the same fix: one server-owned per-card evidence contract. Filing them as two tickets risked building the freshness state and the validity reason on two different schedules when they are one design decision.

The API calculates `data_age_seconds`. The card discards it and shows only the formatted timestamp beside `Bar closed`, which describes candle completion, not freshness. `data_status: ok` means only that the source did not label the bar delayed, incomplete, or otherwise bad — it is not a live-data guarantee. Separately, the API can return `unknown` provider, a null timestamp, and `data_status` / `bar_closed`, but never a single explicit reason a card should or should not be trusted; the richer Multi-Timeframe snapshot already computes bar count, warm-up status, and per-timeframe validity, but Trend Cards render none of it.

**Verified, live — both initial batch probes happened after the extended session closed, so they demonstrate a closed-market presentation case, not the dangerous open-market case:**

- **Original review (2026-09-24):** all ten SPY cards returned `data_status: ok`; the intraday cards were 2,184 seconds old with no stale warning anywhere in the UI contract. The daily and weekly cards were the same age despite representing regular-session / derived data rather than a newly completed session.
- **Re-verified for this prioritization pass (2026-09-24, 20:50 ET — the extended session had ended at 20:00 ET, so the market was genuinely closed):** `GET /api/trend/SPY/batch?timeframes=1m,2m,3m,5m,15m,30m,1h,4h,1d,1wk` — every one of the ten cards carried the identical `timestamp: 2026-09-24T19:59:59.772-04:00`, `data_age_seconds: 3009` (50 minutes old), and `data_status: ok`. That can be a valid final after-hours timestamp for the 1m card, but it cannot establish that every timeframe has the same valid as-of bar.
- **Source-metadata follow-up (2026-09-24, 21:14 ET):** the direct daily source returned `timestamp: 2026-09-24T00:00:00-04:00`, `provider: webull`, `data_status: HISTORICAL`; the direct weekly source returned `timestamp: 2026-09-21T00:00:00-04:00`, `provider: aggregated_from_1d`, `data_status: INCOMPLETE`. The batch Trend payload instead reported the shared 19:59 timestamp, `data_status: ok`, and `bar_closed: true` for both. The provider's daily timestamp convention may legitimately use the start of the trading date, but the mismatch proves the card cannot currently prove its per-timeframe source as-of time or validity. It must not describe the shared timestamp as universally correct.

The bug is therefore broader than a missing closed-session label: the card has neither a `closed_session` state nor a trustworthy per-timeframe evidence envelope. It shows the same bare `Bar closed` label it would show for a genuinely dead feed during market hours, and it can show a timestamp/validity state inherited from a different update. Nothing distinguishes "this is the legitimate last usable bar for this timeframe" from "this is old because the feed died while the market was open" or "this timeframe's source is incomplete." Neither batch probe happened during open market hours, so the dangerous feed-outage case is not yet directly reproduced; it follows from the missing distinction in the contract rather than from a live example.

**Impact:** at a closed market, a trader cannot tell whether a card represents that timeframe's legitimate final usable bar, an incomplete derived bar, or a timestamp inherited from another update. During an open-market feed outage, the same missing state means a trader can read an old intraday setup as current and act after its entry window has passed — the highest-severity version of this bug, because the output looks right while the context is wrong.

**Resolution:** one per-timeframe evidence contract, shared by Trend and Confluence, computed server-side and rendered directly on the card:

- `freshness_state`: `live | recent | stale | closed_session | warming | unavailable`, aware of the selected market session and timeframe — a 1m card and a weekly card cannot share one staleness threshold.
- `age_seconds` with a rendered age badge (`36m old`), kept visually distinct from `bar_closed` (a candle-completion fact, not a freshness fact).
- A per-timeframe `source_as_of`, source `data_status`, `bar_closed`, provider, and session preserved from the actual source; never copy or normalize a latest intraday timestamp onto daily or weekly evidence.
- `valid` / `invalid_reason`, `warmup_bars` / `required_warmup_bars`, so a cold engine, an incomplete derived bar, a stale provider, and a genuinely low-confidence flat signal never look alike.
- Closed-market data presented with its actual per-timeframe source as-of and applicable session-close reference — not as stale live data or a generic shared timestamp.

**Implementation (2026-09-24):** `backend/api/trend/router.py` now constructs one per-timeframe `evidence` envelope from the engine's source metadata, rather than using the signal-generation timestamp as the public as-of time. It emits `freshness_state`, age, source timestamp/as-of, validity, reason, warm-up counts, provider, session, data status, and bar completion. Daily bars dated at midnight are normalized to their regular-session close for freshness/display while preserving the raw source timestamp. `TrendCard` renders the server-owned state and age separately from `Bar closed`; it does not infer freshness in the browser. **Remaining under this ticket:** Confluence must consume the same evidence envelope rather than its parallel snapshot fields, then receive a contract-alignment regression test.

**Tests:** added alongside the fix in `backend/tests/api/test_trend_api.py` and `frontend/src/components/TrendCard.test.tsx`. They cover open-market stale, closed-market last completed bar, delayed provider data, missing timestamp, incomplete weekly-derived data, cold warm-up, and current live evidence. They assert the API contract plus visible state and age badge, not only internal fields.

### TC-03 — 1m, 2m, and 3m Strength is a fixed default rather than a measurement

**Status:** ❌ NOT STARTED
**Where:** indicator-stack selection (`backend/trend/trend_engine.py:370`) and strength derivation (`:846`).

The 1m, 2m, and 3m stacks contain only EMA, RSI, and MACD. They intentionally exclude ADX. `TrendEngine._calculate_trend` initializes `trend_strength` to `MODERATE` and changes it only when ADX exists. Therefore every available 1m, 2m, and 3m card reports `Moderate` Strength, regardless of trend persistence, volatility, or directional agreement.

**Verified, live, twice:**

- **Original review:** live SPY showed 1m and 2m downtrend and 3m sideways, but all three displayed `moderate` strength while their scores and confidence differed materially.
- **Re-verified for this prioritization pass:** SPY 1m `score: -33.2, confidence: 0.55`; 2m `score: -55.4, confidence: 0.70`; 3m `score: -18.9, confidence: 0.38` — three genuinely different setups, all three still `strength: moderate`.

**Impact:** a label that appears to measure trend quality is instead a constant for the shortest cards — on every card, every time, not an edge case. It can lead a trader to overvalue a noisy one-minute move, or to lose trust in every other label on the platform once they notice this one never changes.

**Resolution:** either show `Strength: N/A — short-term momentum` for 1m–3m, or introduce an explicitly named short-horizon persistence metric based on closed bars, ATR-normalized displacement, and directional consistency. Do not call the result ADX-style strength unless it is genuinely measured.

**Tests:** written alongside this fix. Require that sub-5m cards never present a default `Moderate` as measured strength. If a new metric is added, test its flat, breakout, and whipsaw behaviour independently.

---

## High

### TC-08 — The all-timeframe grid and Confluence preset do not share one scope

**Status:** ❌ NOT STARTED
**Where:** Dashboard trend request (`frontend/src/pages/Dashboard.tsx:289`) and Confluence request (`:305`).

Raised from Medium to High in this prioritization pass: this was originally filed as a workflow gap, but it is a correctness bug wearing a workflow-severity label. Trend by Timeframe always fetches ten cards: 1m, 2m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, and 1wk. Confluence uses one selected preset. A day-trading Confluence therefore evaluates 5m through 4h while the adjacent grid presents ten cards with no grouping or visual indication of which cards contributed to the summary.

**Impact:** the natural reading of ten cards sitting next to a Confluence summary is that the summary summarizes the cards. It doesn't. A trader can watch a bullish weekly card and a bullish Confluence badge at the same time and reasonably — and wrongly — conclude the weekly card was part of that conclusion. This is not a hypothetical misreading; the layout invites it.

**Resolution:** group cards as **Timing** (1m–5m), **Structure** (15m–1h), and **Bias** (4h–1wk); highlight the cards in the current preset and offer `Current preset` versus `Show all` modes. Preserve an explicit all-timeframes inspection mode.

**Tests:** switch every preset and assert the contributing cards and explanation update together.

### TC-05 — Confidence and score are not comparable across short and long timeframes

**Status:** ❌ NOT STARTED
**Where:** timeframe-specific stacks (`backend/trend/trend_engine.py:316`) and component scoring (`:830`).

The cards share one 0–100 score and 0–100% confidence presentation, but their inputs differ:

- 1m, 2m, 3m: EMA, RSI, MACD only; without ATR, MACD falls back to a binary positive/negative vote.
- 5m: adds ADX, but still has no ATR, SuperTrend, Bollinger structure, relative volume, or ROC.
- 15m through 1wk: use the fuller stack, including ATR-normalized MACD/ROC, ADX/DI, SuperTrend, Bollinger, relative volume, and ROC.

**Impact:** a 70% confidence or `+65` score on 1m represents much less and different evidence than the same number on 1h or daily. The uniform card design implies false comparability. This is ranked ahead of TC-02 in this pass because it is the more dangerous version of the same "which evidence actually counted" problem for anyone building an Alert or Scanner rule on top of these numbers rather than reading the dashboard by eye: a rule such as "3+ timeframes above +50" currently treats a 1m score built on three unweighted indicators as equal evidence to a daily score built on eight directional components plus ATR normalization, including ADX, SuperTrend, Bollinger, and relative volume.

**Resolution:** either normalize/calibrate score distributions per timeframe with research evidence, or visibly label each card's evidence profile and avoid comparing confidence across the timing, structure, and bias horizons. MTF weighting should use the calibrated quality fields, not an assumed equal meaning for every raw score.

**Tests:** add fixtures with equivalent directional moves across timeframes and assert documented relative expectations. Add calibration / distribution checks before changing thresholds.

### TC-02 — Live intraday Trend cards lose their market-data provider

**Status:** ❌ NOT STARTED
**Where:** bar dispatch (`backend/market_data/services/engine_seeder.py:250`) and Trend metadata (`backend/trend/trend_engine.py:584`).

The ingestion and streaming dispatch path forwards OHLCV, timestamp, data status, and session, but not the originating provider. `TrendEngine.update` therefore receives its default empty provider and stores `unknown` for live intraday bar metadata.

**Verified, live, twice:**

- **Original review:** the live SPY response returned `provider: unknown` for 1m through 4h, `webull` for daily, and `aggregated_from_1d` for weekly.
- **Re-verified for this prioritization pass:** still `unknown` for 1m through 4h. This proves the provenance gap remains unresolved; this probe alone does not establish its relationship to any separate market-data pipeline work, which should be documented with its own code reference if that causality is needed.

**Impact:** in the moment, this is the lowest-urgency High: a discretionary trader reading the dashboard rarely needs to know whether a card came from Webull, a fallback, or a derived bar. It matters for two other reasons that are just as real — establishing trust when something looks wrong (was this card built on good data?), and for anyone about to wire position size or an automated rule to a signal, who should be able to see its provenance before trusting it. Also weakens AI and audit evidence that reuse the same Trend payload.

**Resolution:** extend `dispatch_bar` end-to-end with `provider`; preserve it through resampling and live-bar completion. When a timeframe contains multiple origins, return an explicit `mixed` / source list rather than choosing a misleading single provider. Render provider provenance in a compact readable form.

**Tests:** dispatch a live provider-tagged 1m bar, a resampled 5m bar, and a derived weekly bar; assert each API payload and card label preserves the correct source.

### TC-04 — Per-timeframe `Very Strong` can never be emitted, and may be silently diluting `Strong`

**Status:** ❌ NOT STARTED
**Where:** `TrendStrength` enum (`backend/trend/trend_engine.py:110`) and ADX thresholds (`:885`).

The model and UI expose Weak, Moderate, Strong, and Very Strong. The per-timeframe algorithm only emits Weak, Moderate, or Strong: ADX above 40 is Strong, and there is no branch that assigns Very Strong. Very Strong currently occurs only in the separate legacy overall-trend aggregate, not on an individual Dashboard card.

A second, more concrete risk than "advertises a state no one sees": if the top bucket is structurally unreachable, whatever traffic should have qualified for it — an ADX of 55, say, on a genuinely exceptional trend — still only ever reports `Strong`, the same label as an ADX of 41. `Strong` may therefore be a diluted label that no longer distinguishes a merely-good trend from an exceptional one, which is a methodology problem independent of the missing top label.

**Impact:** the product advertises a state traders will never see, and the strongest established trends are indistinguishable from merely strong ones — a trader cannot tell "this move just started to qualify as strong" from "this is the strongest trend this symbol has had in months."

**Resolution:** either remove `Very Strong` from the per-timeframe UI and type contract, or define a documented criterion such as ADX above 50 plus high directional score and closed-bar confirmation. Before choosing, pull the live ADX distribution for currently `Strong`-classified cards across the watchlist to check whether it is in fact bimodal — i.e., whether `Strong` is quietly absorbing what should be two buckets. That finding should drive the threshold choice, not a guess.

**Tests:** pin the chosen boundaries, including exact threshold values and the no-ADX short-timeframe case. If the distribution check confirms dilution, add a regression against the real sample that motivated the fix.

---

## Medium

### TC-07 — Raw score and classification are available but hidden from Trend Cards

**Status:** ❌ NOT STARTED
**Where:** Trend API returns `score` and `classification` (`backend/api/trend/router.py:62`), but `TrendData` and `TrendCard` omit them (`frontend/src/services/api.ts:64`, `frontend/src/components/TrendCard.tsx:55`).

The same 1m state can appear as `DOWNTREND / 55%` on a Trend Card and `Bearish / −33` in Multi-Timeframe Confluence. Both are valid representations of the same source signal, but the dashboard does not make their relationship visible.

**Impact:** traders cannot reconcile the evidence grid with the confluence summary or see how close a card is to changing classification.

**Resolution:** show a compact, explicit line: `Score −33 · Bearish`, with a tooltip that states score range and thresholds. Keep confidence distinct and describe it as indicator agreement, not win probability.

**Tests:** require positive/negative signs, every score classification boundary, and an accessible tooltip / label.

### TC-09 — Cards lack change history, indicator attribution, key-level context, and chart handoff

**Status:** ❌ NOT STARTED
**Where:** Trend history route (`backend/api/trend/router.py:167`) and card rendering (`frontend/src/components/TrendCard.tsx:77`).

The backend retains trend history and the card has the current direction, strength, confidence, source, and timestamp. It does not show when the state changed, how long it persisted, the principal indicator contributors, relevant available indicator levels, or a direct action to open the matching chart and timeframe.

**Impact:** the card is a static label rather than an auditable decision input. A professional trader needs to distinguish a one-bar reversal attempt from an established move, see *where* price is relative to relevant available indicator levels, and inspect the exact candle context quickly. A raw score of `+65` is less actionable than "price is 40 cents below the SuperTrend flip line."

**Resolution:** show `Changed 2 bars ago` / `Held for 7 bars`, provide an expandable evidence summary (EMA, RSI, MACD, ADX/DI and other available components) with each available component's contribution and relevant level — for example, a SuperTrend flip price or nearest Bollinger edge on timeframes that calculate them — and make the card open the relevant chart at its timeframe. The composite score has no universal single price level that drives it. Use closed-bar history, not polling events, for the duration.

**Tests:** cover a state change, stable continuation, unavailable history, a rendered key-level value, and the chart handoff's selected timeframe.

### TC-10 — No direct TrendCard regressions protect trader-facing truth states

**Status:** ❌ NOT STARTED
**Where:** Dashboard tests mock `TrendCard` (`frontend/src/pages/Dashboard.test.tsx:10`); there is no `TrendCard.test.tsx`.

Focused engine tests cover closed-bar processing and confidence bounds, and registry tests cover warm-up / dispatch behaviour. They do not exercise the rendered card's stale state, provider label, unknown/warming distinction, strength meaning, score display, or accessible text.

**Corrected in this pass:** the original resolution said tests come "before each Batch 1 change," while the original batch plan placed TC-10 in Batch 3, after every other fix — a direct contradiction that would have left Batch 1 unprotected while it was actually being built. There is no separate "testing batch." Each finding's own **Tests** section is written alongside that finding's fix, in whichever batch it falls in. This entry stays open only to track whether a `TrendCard.test.tsx` exists at all and whether it covers every truth-state contract by the time TC-01 through TC-09 are done — it is a completeness check, not a batch of its own.

**Impact:** the dashboard can regress from an auditable data display to a misleading one while backend tests remain green.

**Resolution:** create `TrendCard.test.tsx` with the first fixture as part of TC-01 (the merged freshness/validity contract), extend it with TC-03's and TC-04's fixtures in the same batch those land in, and so on through TC-09. Keep a Dashboard integration test that verifies the selected Confluence preset and card scope remain aligned (TC-08).

**Tests:** this finding's own completion criterion *is* the test coverage — see each other finding's Tests section.

---

## Gaps (not bugs)

- **Role-specific card layout:** a flat ten-card wall creates equal visual weight for a one-minute timing read and a weekly bias. The proposed Timing / Structure / Bias grouping (TC-08) is a workflow enhancement after the truth layer is repaired.
- **Session-aware cadence:** cards need the next expected completed-bar time and closed-session policy, not only a timestamp. A 1m card should be held to a different freshness standard than a weekly card (folded into TC-01's `freshness_state`).
- **Research calibration:** confidence and score thresholds need historical calibration per timeframe and session before they become sizing, screening, or alert criteria (TC-05).
- **User preference:** a trader should be able to save a compact day-trading, swing, or all-timeframe card layout without hiding the evidence used by Confluence.
- **Cross-symbol scanning:** Trend Cards are single-symbol by design. Comparing conviction across a whole watchlist is the Scanner's job, not this card's — noted here so it isn't silently expected of this fix set.
- **Symbol liquidity is a separate, platform-wide gap, not a Trend Card bug:** several watched symbols are thin names. No amount of Trend Card accuracy tells a trader whether they can actually get filled near the price a card is scoring. Out of scope for this document; flagged so it isn't lost.

## Enhancements

1. **Evidence line:** display `Score +65 · Bullish · 55% agreement · 1m old · Valid` in a consistent order on every card.
2. **Strength explanation:** label Strength as `ADX trend persistence` where ADX is used; use a different, explicitly named metric for short timeframes.
3. **Transition chip:** add `New`, `Continuing`, `Pullback`, and `Reversal watch` based on closed-bar history, with the number of bars in state.
4. **Chart handoff with key levels:** clicking a card opens the symbol chart with its timeframe, the relevant indicator overlays enabled, and available contributing indicator levels marked (see TC-09).
5. **Card filter:** add `Current Confluence preset`, `Timing`, `Structure`, `Bias`, and `All` views (TC-08).
6. **Calibration report:** periodically show score/confidence distribution, state-transition frequency, and post-signal outcomes by timeframe, clearly separated from a tradable backtest.

## Verification

### Initial review (2026-09-24)

| Check | Result |
|---|---|
| Local backend health | `200`, service healthy |
| Live Trend API, SPY, all ten timeframes | returned 1m through 1wk signals |
| Live freshness / provenance probe | every returned card had `data_status: ok`; 1m–4h had `provider: unknown`; the intraday payload was 2,184 seconds old at the probe |
| Trend engine and registry focused tests | 36 passed |
| TrendCard component tests | none exist |
| Full backend/frontend suites | not run for this review |

### Trading-desk prioritization review (2026-09-24, 20:50 ET, market closed)

No code changed in this pass — re-verification and re-ranking only, ahead of implementation.

| Check | Result |
|---|---|
| `GET /api/trend/SPY/batch`, all ten timeframes | all ten cards: `timestamp 2026-09-24T19:59:59.772-04:00`, `data_age_seconds: 3009` (50 min), `data_status: ok`. The market was closed (extended session ended 20:00 ET), but the shared batch timestamp cannot prove that every timeframe has a valid identical as-of bar. Direct source probes later returned a daily source timestamp of midnight and an incomplete weekly source timestamp at the start of its period. Reproduces TC-01's missing per-timeframe evidence contract; the dangerous open-market feed-outage case remains inferred from the mechanism, not yet reproduced live. |
| 1m / 2m / 3m Strength vs. score | `moderate` / `moderate` / `moderate` against scores `-33.2` / `-55.4` / `-18.9` and confidence `0.55` / `0.70` / `0.38` — reproduces TC-03 |
| Provider field, 1m–4h | still `unknown` — reproduces TC-02; relationship to separate market-data work is not established by this probe |
| Severity re-ranking | TC-08 raised Medium → High; TC-06 folded into TC-01; TC-10 sequencing contradiction corrected |

## Fix log

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | TC-01 to TC-10 | ❌ NOT STARTED | — | `docs/Version_5/v5_trend_cards_bug_fixes.md` | 36 focused backend tests passed | Initial code/API/live-payload review logged ten findings. |
| 2026-09-24 | TC-01 to TC-10 | ❌ NOT STARTED | — | `docs/Version_5/v5_trend_cards_bug_fixes.md` | — | Trading-desk prioritization pass: TC-06 folded into TC-01; TC-08 raised to High; TC-04 dilution risk added; TC-10 sequencing contradiction corrected; TC-01/TC-03/TC-02 re-verified live. No code changed. |
| 2026-09-24 | TC-01 | ❌ NOT STARTED | — | `docs/Version_5/v5_trend_cards_bug_fixes.md` | — | Correction: both initial batch probes ran after the 20:00 ET extended-session close, so neither reproduced an open-market stale-feed event. Follow-up direct source probes also showed that the batch's shared 19:59 timestamp / `ok` status cannot be treated as universally correct for daily and incomplete weekly evidence. TC-01 now covers the required per-timeframe evidence contract. No code changed. |
| 2026-09-24 | TC-01 | ⚠️ PARTIAL | pending | `backend/api/trend/router.py`, `backend/tests/api/test_trend_api.py`, `frontend/src/components/TrendCard.tsx`, `frontend/src/components/TrendCard.test.tsx`, `frontend/src/services/api.ts`, `frontend/src/styles/App.css` | 119 focused backend tests passed; 5 TrendCard tests passed; production frontend build passed | Added the server-owned Trend evidence contract and rendered it directly. API as-of time now comes from each timeframe's own metadata; daily midnight source stamps normalize to the regular close while retaining their raw timestamp. Confluence adoption remains. |

---

## Reference

Trend by Timeframe is the Dashboard's per-timeframe evidence grid. It reads one shared `TrendEngine` per symbol through the batch Trend API and displays direction, strength, confidence, session, provider, and timestamp for ten timeframes. Multi-Timeframe Confluence reads the same underlying state but applies the selected preset, validity checks, freshness weighting, alignment, and horizon roles to form a summary.

That shared-engine design is valuable: a 15m Trend Card and a 15m Confluence input should describe the same source state. The fixes above are required to make the individual cards equally truthful about freshness, source, strength, evidence breadth, scope, and decision context — in the order a trading desk would actually need them fixed, not the order they were found.
