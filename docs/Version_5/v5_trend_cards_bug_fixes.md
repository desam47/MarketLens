# Version 5 Trend Cards Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (TC-01, TC-03, TC-04, TC-05, and TC-08 complete)
**Status:** Re-prioritized from a trading-desk perspective: what would actually cost a trader money first, not just severity as originally filed. TC-01's shared Trend and Confluence evidence contract, TC-03's measured short-horizon momentum display, TC-04's meaningful `Very Strong` boundary, TC-05's profile-aware score contract, and TC-08's explicit Confluence scope are implemented and covered by focused tests. TC-01 and TC-06 describe one underlying fix — a single server-owned evidence contract — and are tracked together under TC-01.
**Scorecard:** 5 ✅ COMPLETE, 1 ⚠️ PARTIAL, 3 ❌ NOT STARTED, 0 🟡 DEFERRED, 1 folded (TC-06 → TC-01).
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
| TC-01 | Critical | UI + data truth | Cards cannot prove freshness or validity — stale, warming, and invalid data all look like an ordinary closed bar (includes former TC-06) | Verified | ✅ COMPLETE |
| TC-03 | Critical | Methodology | 1m, 2m, and 3m Strength is a fixed default rather than a measurement | Verified | ✅ COMPLETE |
| TC-08 | High | Workflow + data truth | The all-timeframe grid and the selected Confluence preset do not share one scope | Verified | ✅ COMPLETE |
| TC-05 | High | Methodology | Confidence and score are not comparable across short and long timeframes | Validated | ✅ COMPLETE |
| TC-02 | High | Provenance | Live intraday Trend cards lose their market-data provider | Verified | ❌ NOT STARTED |
| TC-04 | High | Methodology | Per-timeframe `Very Strong` can never be emitted, and may be silently diluting `Strong` | Verified | ✅ COMPLETE |
| TC-07 | Medium | UI + contract | Raw score and classification are available but hidden from Trend Cards | Code-read | ❌ NOT STARTED |
| TC-09 | Medium | Workflow | Cards lack change history, indicator attribution, key-level context, and chart handoff | Code-read | ❌ NOT STARTED |
| TC-10 | Medium | Tests | Direct TrendCard regressions are incomplete | Code-read | ⚠️ PARTIAL |
| ~~TC-06~~ | — | — | ~~Cards cannot explain warming, invalid, delayed, or unavailable evidence~~ — folded into TC-01, same evidence contract | Verified | folded |

**Trading-desk order — what would actually cost a trader money first, not filing order:**

1. **Batch 1 — trader-facing truth.** TC-01 (the merged freshness + validity contract — this one fix also resolves former TC-06), then TC-03, then TC-04. TC-10's fixtures are written *inside* this batch, one per finding, as each is built — not deferred to a trailing batch. A truth-state fix with no regression test protecting it the same day is not actually fixed; the original plan's own TC-10 text said "before Batch 1" while its batch placement said "after Batch 3." This corrects that contradiction.
2. **Batch 2 — evidence a screener or alert can trust, not just a person reading the dashboard.** TC-08 first: the natural reading of ten cards sitting beside a Confluence badge is that the badge summarizes the cards, and it doesn't say otherwise when it isn't true. Then TC-05: uncalibrated cross-timeframe comparison is the more dangerous version of the same "which evidence actually counted" problem, and is the one most likely to silently corrupt an Alert or Scanner rule built on these numbers, not just a glance at the screen. Then TC-02.
3. **Batch 3 — depth and professional workflow.** TC-07, TC-09.

---

## Critical

### TC-01 — Cards cannot prove freshness or validity: stale, warming, and invalid data all look like an ordinary closed bar

**Status:** ✅ COMPLETE
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

**Implementation (2026-09-24):** `backend/trend/evidence.py` owns one per-timeframe `evidence` contract from the engine's source metadata, rather than using the signal-generation timestamp as the public as-of time. It emits `freshness_state`, age, source timestamp/as-of, validity, reason, warm-up counts, provider, session, data status, and bar completion. Daily bars dated at midnight are normalized to their regular-session close, and completed bars derived from the weekly Monday bucket are normalized to that week's final trading-session close, while preserving the raw source timestamp. Trend and Confluence both consume this module: `TrendCard` renders the server-owned state and age separately from `Bar closed`, while Confluence uses it for snapshot validity/weighting and renders each input's evidence state. The browser does not infer freshness.

**Tests:** added alongside the fix in `backend/tests/api/test_trend_api.py`, `backend/tests/api/test_mtf_api.py`, `frontend/src/components/TrendCard.test.tsx`, and `frontend/src/components/ConfluenceCard.test.tsx`. They cover open-market stale, closed-market last completed bar, delayed provider data, missing timestamp, incomplete weekly-derived data, completed-week close normalization, cold warm-up, current live evidence, and Trend/Confluence daily source-as-of alignment. They assert the API contract plus visible state and age badge, not only internal fields.

### TC-03 — 1m, 2m, and 3m Strength is a fixed default rather than a measurement

**Status:** ✅ COMPLETE
**Where:** indicator-stack selection (`backend/trend/trend_engine.py:370`) and strength derivation (`:846`).

The 1m, 2m, and 3m stacks contain only EMA, RSI, and MACD. They intentionally exclude ADX. `TrendEngine._calculate_trend` initializes `trend_strength` to `MODERATE` and changes it only when ADX exists. Therefore every available 1m, 2m, and 3m card reports `Moderate` Strength, regardless of trend persistence, volatility, or directional agreement.

**Verified, live, twice:**

- **Original review:** live SPY showed 1m and 2m downtrend and 3m sideways, but all three displayed `moderate` strength while their scores and confidence differed materially.
- **Re-verified for this prioritization pass:** SPY 1m `score: -33.2, confidence: 0.55`; 2m `score: -55.4, confidence: 0.70`; 3m `score: -18.9, confidence: 0.38` — three genuinely different setups, all three still `strength: moderate`.

**Impact:** a label that appears to measure trend quality is instead a constant for the shortest cards — on every card, every time, not an edge case. It can lead a trader to overvalue a noisy one-minute move, or to lose trust in every other label on the platform once they notice this one never changes.

**Resolution:** introduce an explicitly named short-horizon momentum metric based on closed bars, ATR-normalized displacement, and directional consistency. Do not call the result ADX-style strength unless it is genuinely measured.

**Implementation (2026-09-24):** 1m, 2m, and 3m retain ATR solely to measure short-horizon momentum. The engine evaluates the latest four valid closed bars: net close-to-close displacement in ATRs plus directional consistency (net movement divided by total movement). It emits `Choppy`, `Developing`, or `Persistent`, separately from the legacy ADX-style strength enum. The card now labels the field **Momentum** on these three timeframes and displays `Awaiting bars` until enough closed-bar and ATR evidence exists; it never presents default `Moderate` as a measured strength.

**Tests:** backend breakout, flat, whipsaw, insufficient-bars, and missing-ATR cases; API contract coverage for the separate momentum fields; and card assertions that 1m renders `Momentum` / `Persistent` or `Awaiting bars`, never `Strength` / default `Moderate`.

---

## High

### TC-08 — The all-timeframe grid and Confluence preset do not share one scope

**Status:** ✅ COMPLETE
**Where:** Dashboard Trend-by-Timeframe rendering (`frontend/src/pages/Dashboard.tsx`) and `TrendCard` (`frontend/src/components/TrendCard.tsx`).

Raised from Medium to High in this prioritization pass: this was originally filed as a workflow gap, but it is a correctness bug wearing a workflow-severity label. Trend by Timeframe always fetches ten cards: 1m, 2m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, and 1wk. Confluence uses one selected preset. A day-trading Confluence therefore evaluates 5m through 4h while the adjacent grid presents ten cards with no grouping or visual indication of which cards contributed to the summary.

**Impact:** the natural reading of ten cards sitting next to a Confluence summary is that the summary summarizes the cards. It doesn't. A trader can watch a bullish weekly card and a bullish Confluence badge at the same time and reasonably — and wrongly — conclude the weekly card was part of that conclusion. This is not a hypothetical misreading; the layout invites it.

**Resolution:** the Dashboard now treats the current `confluence.timeframe_signals` keys as authoritative: they are the exact inputs the backend used for that response. `Current preset` is the default view and renders only those cards. `Show all` preserves inspection of every available card, but labels contributor cards **Confluence input** and every other card **Not in preset**. Cards are grouped as **Timing** (1m–5m), **Structure** (15m–1h), and **Bias** (4h–Weekly), so their trading role is explicit. While a new Confluence request is in flight, the UI falls back only to the documented selected-preset map and never trusts a response for a different preset. The backend's `all` preset deliberately excludes 2m and 3m; they remain inspectable and are visibly marked out of scope rather than silently implied as inputs.

**Verification:** live browser verification on 2026-09-24 with the Day Trading response showed its five displayed contributor cards (5m, 15m, 30m, 1h, 4h) exactly matched the backend Confluence signal list. In `Show all`, 1m, 2m, 3m, daily, and weekly remained visible but were explicitly labelled **Not in preset**.

**Tests:** Dashboard integration test switches Scalper, Day Trading, Swing Trading, and All Timeframes preset responses. It asserts contributor roles, filtered/default scope, full inspection scope, grouping, and the intentional 2m/3m exclusion from the server's All Timeframes preset.

### TC-05 — Confidence and score are not comparable across short and long timeframes

**Status:** ✅ COMPLETE
**Where:** scoring-profile contract (`backend/trend/trend_engine.py`), Trend and Confluence serializers, Multi-Timeframe aggregation (`backend/multitimeframe/multi_timeframe_engine.py`), and the Trend/Confluence cards.

The cards share one 0–100 score and 0–100% confidence presentation, but their inputs differ:

- 1m, 2m, 3m: EMA, RSI, MACD only; without ATR, MACD falls back to a binary positive/negative vote.
- 5m: adds ADX, but still has no ATR, SuperTrend, Bollinger structure, relative volume, or ROC.
- 15m through 1wk: use the fuller stack, including ATR-normalized MACD/ROC, ADX/DI, SuperTrend, Bollinger, relative volume, and ROC.

**Impact:** a 70% confidence or `+65` score on 1m represents much less and different evidence than the same number on 1h or daily. The uniform card design implies false comparability. This is ranked ahead of TC-02 in this pass because it is the more dangerous version of the same "which evidence actually counted" problem for anyone building an Alert or Scanner rule on top of these numbers rather than reading the dashboard by eye: a rule such as "3+ timeframes above +50" currently treats a 1m score built on three unweighted indicators as equal evidence to a daily score built on eight directional components plus ATR normalization, including ADX, SuperTrend, Bollinger, and relative volume.

**Resolution:** this uses the safer of the two proposed paths: it makes the current profiles explicit and removes uncalibrated magnitude from Confluence aggregation. Every Trend API response now declares one of three profiles: **Directional core** (1m/2m/3m: EMA, RSI, MACD), **Directional + ADX** (5m), or **Full technical stack** (15m through weekly: eight components). The contract explicitly says that card agreement is weighted indicator agreement, not win probability, and that raw score/agreement is not cross-timeframe calibrated.

Trend Cards now show **Agreement** rather than a bare Confidence label, plus the profile and number of inputs. Confluence exposes the same profile per timeframe. Its former raw-score aggregate is retained on the compatible `quality_weighted_score` field but now means a **Directional Vote**: valid timeframe direction (-1/0/+1), weighted only by preset/horizon and source usability (freshness, warm-up, completed bar). It deliberately excludes both raw score magnitude and profile-specific agreement. This prevents a `+65` 1m core-stack score from carrying the same aggregation meaning as a `+65` daily full-stack score.

No outcome calibration is claimed. Historical calibration by timeframe and session remains required before scores or agreements become position-sizing, screening, or alert thresholds; that is research work, not a UI relabel.

**Tests:** profile mapping and non-comparability contract; Trend batch API serialization for 1m/5m/1h; a Confluence regression proving swapping raw score magnitudes cannot change the directional-vote aggregate; direct TrendCard rendering of Agreement/profile semantics.

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

### TC-04 — Per-timeframe `Very Strong` was unreachable and could silently dilute `Strong`

**Status:** ✅ COMPLETE
**Where:** `TrendStrength` enum (`backend/trend/trend_engine.py:110`) and ADX thresholds (`:885`).

The model and UI expose Weak, Moderate, Strong, and Very Strong. The per-timeframe algorithm only emits Weak, Moderate, or Strong: ADX above 40 is Strong, and there is no branch that assigns Very Strong. Very Strong currently occurs only in the separate legacy overall-trend aggregate, not on an individual Dashboard card.

A second, more concrete risk than "advertises a state no one sees": if the top bucket is structurally unreachable, whatever traffic should have qualified for it — an ADX of 55, say, on a genuinely exceptional trend — still only ever reports `Strong`, the same label as an ADX of 41. `Strong` may therefore be a diluted label that no longer distinguishes a merely-good trend from an exceptional one, which is a methodology problem independent of the missing top label.

**Impact:** the product advertises a state traders will never see, and the strongest established trends are indistinguishable from merely strong ones — a trader cannot tell "this move just started to qualify as strong" from "this is the strongest trend this symbol has had in months."

**Resolution:** define a documented `Very Strong` criterion: ADX > 50, DI directional balance ≥ 0.50, and absolute composite score ≥ 60 on a closed, valid source bar. This prevents high ADX alone from being mistaken for an exceptional directional move.

**Live distribution used (2026-09-24):** the 23 enabled active-watchlist symbols produced 10 ADX-`Strong` cards. Their ADX range was 40.27–52.94 (median 42.47); only one exceeded 50. The sole candidate, META 4h, also had DI balance 0.52 and composite score +63.28. This supports a narrow exceptional bucket rather than arbitrary re-labelling of ordinary `Strong` cards.

**Implementation (2026-09-24):** the engine now emits `Very Strong` only when all three gates pass. Its confidence multiplier is 1.30 (versus 1.20 for `Strong`), capped at 100%. Existing card UI renders the state directly; 1m–3m remain governed by TC-03's distinct Momentum field because they have no ADX.

**Tests:** engine tests prove that a qualifying directional breakout reaches `Very Strong`, while high ADX with insufficient DI balance remains `Strong`; a card test renders `Very Strong` on a 4h card. TC-03 separately protects the no-ADX short-timeframe path.

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

### TC-10 — Direct TrendCard regressions are incomplete

**Status:** ⚠️ PARTIAL
**Where:** Dashboard tests mock `TrendCard` (`frontend/src/pages/Dashboard.test.tsx:10`); direct coverage now exists in `frontend/src/components/TrendCard.test.tsx`, but only for completed TC-01 and TC-03 contracts.

Focused engine tests cover closed-bar processing and confidence bounds, and registry tests cover warm-up / dispatch behaviour. Direct card tests now cover evidence state/age, unavailable and warming states, and short-horizon momentum wording. They do not yet cover provider label, score/classification display, history, or chart handoff.

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
| 2026-09-24 | TC-01 | ✅ COMPLETE | `03e5b3a` | `backend/trend/evidence.py`, `backend/multitimeframe/multi_timeframe_engine.py`, `backend/api/multitimeframe/router.py`, `backend/tests/api/test_mtf_api.py`, `backend/tests/api/test_trend_api.py`, `frontend/src/components/ConfluenceCard.tsx`, `frontend/src/components/ConfluenceCard.test.tsx` | 140 focused Trend/MTF/AI-contract backend tests passed; 8 TrendCard/ConfluenceCard tests passed; production frontend build passed | Moved the contract into a shared backend module. Confluence now uses it for validity/quality weighting, serializes the identical per-timeframe payload, and visibly labels each contributing timeframe's evidence state. Completed weekly derived bars now use their final trading-session close as the trader-facing as-of time, while retaining the raw Monday bucket key. |
| 2026-09-24 | TC-03 | ✅ COMPLETE | `841baff` | `backend/trend/trend_engine.py`, `backend/api/trend/router.py`, `backend/tests/trend/test_trend_engine.py`, `backend/tests/api/test_trend_api.py`, `frontend/src/components/TrendCard.tsx`, `frontend/src/components/TrendCard.test.tsx`, `frontend/src/services/api.ts` | 189 focused Trend/MTF/AI-contract backend tests passed; 10 TrendCard/ConfluenceCard tests passed; production frontend build passed | Replaced the false short-timeframe strength display with a closed-bar, ATR-normalized momentum measure: Choppy, Developing, or Persistent. Retaining ATR for this measure does not change the existing short-timeframe directional score. TC-10 is now partial because direct card coverage exists for TC-01 and TC-03. |
| 2026-09-24 | TC-04 | ✅ COMPLETE | `0c894f6` | `backend/trend/trend_engine.py`, `backend/tests/trend/test_trend_engine.py`, `frontend/src/components/TrendCard.test.tsx`, `docs/Version_5/v5_trend_cards_bug_fixes.md` | 191 focused Trend/MTF/AI-contract backend tests passed; 11 TrendCard/ConfluenceCard tests passed; production frontend build passed | Made `Very Strong` reachable only for ADX > 50 plus DI-balance and composite-score confirmation, using the active-watchlist distribution to select the boundary. |
| 2026-09-24 | TC-08 | ✅ COMPLETE | pending | `frontend/src/pages/Dashboard.tsx`, `frontend/src/pages/Dashboard.test.tsx`, `frontend/src/components/TrendCard.tsx`, `frontend/src/styles/App.css`, `docs/Version_5/v5_trend_cards_bug_fixes.md` | 15 focused Dashboard/TrendCard/ConfluenceCard tests passed; production frontend build passed; live browser verified Day Trading scope and Show all roles | Made the actual backend Confluence input set visible and filterable, with explicit roles for all inspected cards. |
| 2026-09-24 | TC-05 | ✅ COMPLETE | pending | `backend/trend/trend_engine.py`, `backend/api/trend/router.py`, `backend/multitimeframe/multi_timeframe_engine.py`, `backend/api/multitimeframe/router.py`, focused backend tests, `frontend/src/components/TrendCard.tsx`, `frontend/src/components/ConfluenceCard.tsx`, frontend API types/styles/tests, `docs/Version_5/v5_trend_cards_bug_fixes.md` | 99 focused backend tests passed; 16 focused frontend tests passed; production frontend build passed | Declared profile-specific score semantics, rendered them, and changed Confluence from raw-score/agreement aggregation to source-quality-weighted directional votes. Local live API check was unavailable because port 5001 was not listening; API behavior is covered by FastAPI contract tests. |

---

## Reference

Trend by Timeframe is the Dashboard's per-timeframe evidence grid. It reads one shared `TrendEngine` per symbol through the batch Trend API and displays direction, strength, confidence, session, provider, and timestamp for ten timeframes. Multi-Timeframe Confluence reads the same underlying state but applies the selected preset, validity checks, freshness weighting, alignment, and horizon roles to form a summary.

That shared-engine design is valuable: a 15m Trend Card and a 15m Confluence input should describe the same source state. The fixes above are required to make the individual cards equally truthful about freshness, source, strength, evidence breadth, scope, and decision context — in the order a trading desk would actually need them fixed, not the order they were found.
