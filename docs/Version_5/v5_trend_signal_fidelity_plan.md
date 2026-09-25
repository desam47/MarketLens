# Version 5 — Trend Signal Fidelity Plan: the Gated Hybrid model

**Created:** 2026-09-25
**Status:** 📋 PLAN — decisions locked 2026-09-25; no code changed yet. This replaces the earlier
"tweak the weighted composite" draft with a new per-timeframe **scoring model** (the *Gated Hybrid*),
because a professional-grade, realtime trend read needs a different architecture — not new weights.
**Goal:** each Trend by Timeframe card shows a professional-grade, **realtime** signal a desk can act
on — direction, whether a trend actually exists, how strong/clean it is, whether it's confirmed or
tiring, where the stop sits, and how it fits the bigger picture — on every timeframe, especially the
fast ones (1m/2m/3m/5m) that are the thinnest today.
**Related:** [Trend Cards fixes](v5_trend_cards_bug_fixes.md) (TC-01…TC-10, complete). That work made
the current limitations *honest*; this plan fixes the *methodology*.

Status legend: ✅ done · 📋 planned · 🔬 research.

---

## Why — the two failure modes, seen live on AAPL (2026-09-25)

The current model is a **flat weighted vote of up to 8 equal-ish indicators**, thresholded for
direction. Real AAPL data shows its two failure modes:

1. **Saturation on fast timeframes.** 1m/2m/3m read `−100 · strong_bearish · 100% agreement` because
   EMA, binary-MACD, and RSI are all pinned at ±1. No professional believes a 1-minute chart is
   "maximally bearish, 100% certain."
2. **Mushy "sideways" that hides real events.** 15m→1wk all read `sideways, +12 to −20` — including
   4h/1d/1wk, which had **just flipped SuperTrend up** (price 0.008–0.15 ATR above the line) while DI
   was still strongly bearish. A fresh, unconfirmed higher-timeframe reversal was buried as "sideways."

Root cause: **momentum oscillators (MACD/RSI) and a trend-follower (SuperTrend) are averaged as
co-equal voters.** They answer different questions and shouldn't be summed. The fix is to *stage* them.

---

## The Gated Hybrid scoring model

Each timeframe is scored in four stages. Every stage runs on closed bars inside
`_update_from_bar`/`_calculate_trend` (the existing realtime path), so cards stay realtime.

### Stage 1 — Direction gate: **SuperTrend** (all timeframes)
SuperTrend decides up / down / just-flipped, and its line is the **stop**. It replaces the
threshold-on-weighted-average as the direction authority. Requires adding SuperTrend to 1m/2m/3m/5m
(today only 15m+ have it). `band_distance_atr` (already exposed) measures how far price sits from the
flip — i.e. trend maturity and stop distance.

### Stage 2 — Trend-existence gate: **ADX level (+ slope, E3)**
ADX decides whether there's a trend to trust at all. Below the range threshold (≈20–25) the card reads
**"No trend / range"** regardless of SuperTrend's direction — this kills SuperTrend's chop-whipsaw, its
worst weakness. ADX *slope* (E3) adds strengthening vs fading. Requires exposing the raw ADX value
(payload only carries the `strength` band today).

### Stage 3 — Conviction score: the composite, **repurposed**
The old weighted vote (EMA, RSI, ATR-normalized MACD, DI, Bollinger, ROC, volume) is **no longer the
direction** — it becomes the **conviction magnitude within SuperTrend's direction**. MACD is
ATR-normalized on every timeframe (no more binary ±1), so conviction is graded, not saturated. DI
agreement, band distance, and momentum set how strong/clean the move is.

**Output (keeps the existing API contract, new semantics):** the card still emits `score` (−100..+100)
and `classification`, but now `score = superTrendSign × regimeFactor × convictionMagnitude × 100`:
- `regimeFactor` collapses toward 0 when ADX says "range" → range markets read near-neutral, not a
  confident false direction.
- `convictionMagnitude` is graded by band distance + DI + normalized momentum + volume → no ±100
  saturation on fast cards.
Direction, regime, conviction, and stop are all separately visible; the composite score is a summary,
not the arbiter.

### Stage 4 — Enhancement layers
**Core (ship with the model — data already exists, near-free):**
- **E1 — Volume participation.** `relative_volume` confirms/denies the move: `↑ confirmed by volume`
  vs `↑ on fading volume`. A flip/trend on weak volume is flagged low-conviction.
- **E4 — Higher-timeframe bias tag.** Each card shows a compact `with 1h ↑` / `counter to 1h ↓` tag
  from a designated higher TF (one engine holds all TFs — cheap). Not the full Confluence summary —
  a one-line context so each card is actionable standalone.
- **E5 — Trend maturity + risk.** `held N bars` (shipped) + `band_distance_atr` + Bollinger %B →
  **Fresh / Healthy / Extended**, plus the stop as risk ("stop 0.9 ATR away"). Turns a signal into a
  setup: don't-chase vs fresh-tight-stop.

**Fast-follow (small new logic, validate separately):**
- **E3 — ADX slope.** Trend strengthening vs fading (store last few ADX values).
- **E2 — Momentum divergence / exhaustion.** Price-vs-RSI/MACD divergence + DI disagreement → an early
  `⚠ momentum not confirming / diverging` warning *before* SuperTrend flips. Highest-value new signal;
  patches SuperTrend's lag. Needs careful divergence logic and its own validation.

### Realtime confirmation (was Change C)
SuperTrend's own confirmation buffer debounces 15m+. On 1m/2m/3m add an N=2 closed-bar confirm before
committing a *headline* flip, but surface the developing turn **immediately** as
`reversal forming — k/2` (from `_trend_state_runs`). The live read is never hidden; only the committed
label is debounced (realtime requirement R2). E2 divergence feeds the same "forming/unconfirmed" state.

---

## Worked example — AAPL, all ten cards (live components, 2026-09-25)

Built from live SuperTrend direction + `band_distance_atr`, ADX (via `strength`), DI/RSI/MACD signals.
Fast TFs (1m–5m) get SuperTrend/ADX under the model; their rows read from current evidence and are the
most illustrative.

```
1m   ▼ DOWNTREND · confirmed · moderate      momentum confirms; graded, not −100/100%
2m   ▼ DOWNTREND · confirmed · moderate       (same)
3m   ▼ DOWNTREND · confirmed · moderate       (same)
5m   ◆ NO TREND / RANGE · lean down          ADX weak → gate; kills today's false "+38.6 BULLISH"
15m  ▲ SLOW UPTREND · low conviction         ST up ≥3 ATR (mature) but ADX weak
30m  ▲ UPTREND · low-moderate                ST up mature; ADX moderate; DI mildly against
1h   ▼ SLOW DOWNTREND / range · lean down     ST down; ADX weak
4h   ▲ UPTREND · FRESH FLIP · UNCONFIRMED ⚠   0.008 ATR above stop; ADX strong; DI −0.95; momentum diverging
1d   ▲ UPTREND · fresh · unconfirmed          0.05 ATR above stop; ADX strong; DI −0.86
1wk  ▲ UPTREND · building · low conviction    0.15 ATR above stop; ADX strong; DI −0.73
```

**The read a desk gets:** intraday down (1m/2m/3m), chop on 5m/1h, and a **fresh, unconfirmed
higher-timeframe bullish reversal** (4h/1d/1wk just flipped up, momentum not yet confirming). Today's
model buries all of that as "−100s" and "sideways." The **4h card with E1/E2/E4/E5** reads:

```
▲ UPTREND — FRESH FLIP (price 0.01 ATR above stop)      ← direction + maturity (E5)
⚠ unconfirmed: DI −0.95, momentum diverging             ← divergence (E2)
participation: weak / volume not expanding               ← volume (E1)
bias: aligned with 1d ↑                                  ← HTF tag (E4)
maturity: FRESH · stop right here, tight risk            ← R:R (E5)
```
That is a decision, not a data dump: a real reversal starting, momentum not confirming, weak
participation, but tiny risk because the stop is right here.

---

## What this requires in code

| Piece | Change | Data status |
|---|---|---|
| SuperTrend on 1m/2m/3m/5m | add indicator (5m-/fast-tuned ATR period + multiplier) | new instances |
| ADX on 1m/2m/3m | add for the regime gate | new instances |
| ATR on 1m/2m/3m/5m fed to MACD | conviction MACD normalized everywhere | ATR exists on 1m/2m/3m; add to 5m |
| Raw ADX value in payload | for the gate + slope + card | derive from indicator |
| Gated score in `_calculate_trend` | SuperTrend sign × ADX regime × conviction | rewrite scoring |
| E1 volume modifier | fold `relative_volume` into conviction | exists |
| E4 HTF bias tag | each card reads a designated higher TF's state | same engine |
| E5 maturity/R:R | `held_bars` + band distance + %B → Fresh/Healthy/Extended + stop | all exist |
| E3 ADX slope | store last few ADX values per TF | small |
| E2 divergence | price-vs-RSI/MACD divergence detector | new logic |

Weights already exist for all components ([settings.py:564-578](../../backend/config/settings.py#L564))
and the scorer renormalizes by active weight, so no new config is needed for the conviction layer.

---

## Realtime requirements (must hold — R1–R4)

Delivery today is realtime: `_update_from_bar` → `_invalidate_output_caches` pops `_trend_cache` → the
WebSocket pushes a bar event → the card refetches a freshly-computed signal; the shortest TF also
updates intra-bar per tick.
- **R1** — all model logic stays on the `_calculate_trend`/`_update_from_bar` path; no batch path, no
  cache outliving a bar.
- **R2** — confirmation/divergence never *hide* the signal: the developing turn shows immediately as
  `reversal forming`, only the committed headline is debounced.
- **R3** — SuperTrend/ADX/Bollinger advance on their own bar close (correct); tick path for the
  shortest TF unaffected; added per-bar compute is negligible.
- **R4** — measure bar-close → card-update latency with the flag on; it must stay in the current
  envelope (< ~1–2s); the "forming" chip must appear on the *same* bar the raw signal turns.

---

## Blast radius & migration

This changes the **direction methodology on every timeframe** (SuperTrend-led, not composite-threshold),
so — unlike the earlier draft — higher-TF directions can also change. Scores/directions feed
`historical_signals`, backtests, and alerts (`trend_crosses_above_70`, `trend_direction_changes`,
`trend_strengthens/weakens`, alignment), so short- *and* long-TF alert timing will shift.

**Approach (locked):** implement behind a `TREND_SIGNAL_V2` settings flag (default off) → run a
**shadow period** computing old + new in parallel and logging both → validate (below) → flip the flag →
run `scripts/relabel_signals.py --apply` once so `historical_signals` matches (it already replays each
stored bar through a fresh engine, with backup/`--restore`). Reversible, observable, no surprise.

---

## Validation plan (without outcome calibration — that's SF-03)

The earlier "higher-TF bit-for-bit unchanged" check no longer applies — we are deliberately changing
direction on all TFs. Instead:
1. **Direction/regime diff, old vs new, on the live watchlist:** every card, per TF, both models
   side-by-side. Confirm the differences are the *intended* improvements (no more ±100 saturation; range
   markets flagged; fresh flips surfaced), reviewed on charts.
2. **Saturation gone:** new fast-TF score distribution has no ±100 pile-up.
3. **Range detection:** in known chop, cards read "No trend / range," not a confident direction.
4. **Fresh-flip surfacing:** the AAPL-style case (SuperTrend flip + DI disagreement) reads
   "fresh/unconfirmed," not "sideways."
5. **Whipsaw reduction:** short-TF headline flips over a replay window drop with confirmation, without
   over-lagging real reversals.
6. **Realtime latency (R1–R4):** bar-close → card-update within envelope; "forming" chip on the turning
   bar; cache-pop + WS push + per-tick flow intact.
7. **Backtest behavior diff** (behavioral, not P&L) and **manual trader review** across sessions.

---

## Test plan

- **Stage 1/2:** SuperTrend decides direction on all TFs; ADX below the gate forces "No trend / range";
  a fresh flip (small band distance) reads "fresh/unconfirmed."
- **Stage 3:** conviction MACD varies with histogram magnitude (not sign); range markets score near
  neutral; no ±100 on fast TFs.
- **E1:** a move on low relative volume is flagged weak participation.
- **E4:** each card carries the correct higher-TF bias tag.
- **E5:** Fresh vs Extended states from held_bars + band distance + %B; stop distance rendered.
- **E3:** rising vs falling ADX changes the strengthening/fading read.
- **E2:** price new-high with RSI lower-high flags divergence before the flip; a clean trend does not.
- **Confirmation/R2:** headline waits N bars; "reversal forming — 1/2" appears on the first turning bar.
- **Migration:** `relabel_signals.py` dry-run/apply/restore succeed against the new engine.

---

## Sequencing

1. **Stage 1+2 + Stage 3 core** behind `TREND_SIGNAL_V2` — SuperTrend/ADX on all TFs, gated score,
   ATR-normalized conviction MACD. (Subsumes the old Changes A/B; direction now SuperTrend-led.)
2. **E1 + E4 + E5** — near-free, make every card decision-ready.
3. **Warmup gate** per profile (old Change D) — cheap correctness guard.
4. **E3 (ADX slope)**, then **E2 (divergence)** — fast-follows with their own validation.
5. Shadow-validate → flip the flag → `relabel_signals.py --apply`.
6. **SF-03 calibration** — separate research doc, after the model is stable (calibrate the *new*
   signal, not the old saturated one).

---

## Decisions (locked 2026-09-25)

1. **Model:** Gated Hybrid — SuperTrend direction, ADX regime gate, composite as conviction, E-layers.
   Not pure SuperTrend (whipsaw/no-momentum) and not the flat composite (saturation/mushy sideways).
2. **Migration:** flag → shadow → validate → flip → re-label history.
3. **Fast-TF confirmation:** N=2 on 1m/2m/3m; 5m relies on its new SuperTrend; developing turn shown
   live (R2).
4. **5m:** full stack (first-class), 5m-tuned SuperTrend params.
5. **Enhancements:** E1/E4/E5 in the core now; E3 then E2 as fast-follows.
6. **Alerts:** audit short-/long-TF trend rules, measure firing-rate delta in shadow, note in release
   log; thresholds are fixed (±70) so nothing to migrate.
7. **Calibration (SF-03):** next version, non-blocking — it must run against this model, not the old one.

## Open items

- Exact gate/threshold tuning (ADX range cutoff, band-distance "fresh vs established," divergence
  lookback) — set in shadow, per timeframe/session.
- Raw ADX value must be added to the trend payload (currently only the `strength` band ships).
- E4 boundary vs the Confluence card: a compact per-card tag only; the full alignment summary stays
  Confluence's job.
