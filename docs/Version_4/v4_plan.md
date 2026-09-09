# Version 4 — Charts

**Date:** 2026-09-09
**Last updated:** 2026-09-09
**Status:** Planned. No items started yet.
**Scope:** Opens with the one item Version 3 never got to (chart drawing tools, more chart types, more indicators). Additional phases get added here as they're scoped, the same way Version 3 grew from its original five-area plan to thirteen phases.

---

## Goals

1. Give the chart a real drawing toolbar — click-to-place trend lines and horizontal lines, instead of the current manual-timestamp-entry form.
2. Add chart types beyond candlestick (line, area, Heikin Ashi).
3. Expand the indicator set and give each indicator a sensible home (overlay vs. separate pane).
4. Persist per-symbol chart preferences so returning to a symbol doesn't reset chart type/indicators.

**Non-goals (deferred beyond v4 unless re-scoped):**
- Renko / Kagi / Point-and-Figure chart types — real data-transformation work, not just rendering; only worth it if v1 lands cleanly and there's demand.
- Re-attempting a TradingView Charting Library integration. One was built in Version 3 (commit `9598dae` — `TradingViewChart`/`TradingViewMultiChart` components, a UDF datafeed, a `/api/tv/*` backend router, a `REACT_APP_USE_TRADINGVIEW` rollback flag) but none of it is present in the current tree; `lightweight-charts` + `CandlestickChart.tsx` is the live implementation and this plan builds on that, not on TV. Worth knowing before reaching for it again — the rollback flag existed because it was going to need one.

---

## Phase 4.1 — Charts

Carried forward verbatim from Version 3's Phase 3.4 (never started there — see `docs/Version_3/v3_plan.md` for the original, and `docs/Version_3/phase_audit_v3.md`'s scorecard for why it was deferred rather than dropped).

**Why now:** Charts are the most-visited surface. Drawing tools require manual timestamp entry (broken UX). Only one chart type (candlestick). Limited indicator set.

**Drawing decisions confirmed (from the original v3 scoping — re-confirm still current before building):**
- Types v1: **Trend line** (2-point) + **Horizontal line** (1-point, price-level) — scope kept to these two
- Activation: **Floating toolbar** inside the chart card (top-left strip of buttons)
- Interaction: **Click → click** to place. **Esc** cancels mid-draw. One click for horizontal line, two for trend line.
- Storage: **DB-backed** (existing CRUD API, unchanged) — drawing saves to server immediately after second click
- Lock toggle: small icon button in the toolbar; when locked, chart ignores drawing clicks

### Items

#### 4.1.1 Drawing tools: click-to-place (trend line + horizontal line)
- **Current state:** `DrawingToolsPanel.tsx` form requires typing start/end timestamps and prices. Clunky.
- **New behavior (v1):**
  1. Floating toolbar inside `CandlestickChart` card (top-left): `[T↗] [—] 🔓`
  2. Click a tool button → enter "draw mode" for that tool (button highlights active)
  3. Click on the chart → place point 1 (shows a marker dot)
  4. Click again → place point 2, drawing commits → POST to API → added to the sidebar list
  5. Press Esc → cancel draw mode, remove marker, no API call
  6. Drawing lock (🔓→🔒): when locked, clicks are ignored (prevent accidental edits)
- **Implementation:**
  - New file: `frontend/src/components/chartInteractions.ts` — pure `pixelToCoord(chart, x, y) -> {time: number, price: number}` using `chart.timeScale().coordinateToTime()` and `chart.priceScale().coordinateToPrice()`
  - Add `onChartClick(time, price)` callback prop to `CandlestickChart`; when a drawing tool is active and chart is unlocked, emit this
  - `CandlestickChart` state machine: `idle | placing-start | placing-end | drawing-locked`
  - On `placing-start` click: show a temporary marker div; transition to `placing-end`
  - On `placing-end` click: call `api.createDrawingTool(...)`, reset to `idle`
  - On Esc keydown: if in `placing-*`, remove marker, transition to `idle`
  - After save: refresh `DrawingToolsPanel` list (existing `fetchDrawings` refetch)
- Drawing types supported in v1: `trend_line` (needs 2nd click), `horizontal_line` (needs 1 click — price level only, no time)

#### 4.1.2 Chart toolbar: drawing lock toggle + tool activation
- Add lock state to `CandlestickChart`: `const [drawLock, setDrawLock] = useState(false)`
- Toolbar buttons: `Trend Line`, `Horizontal`, lock icon
- Active tool indicator (one at a time, cleared on Esc or on drawing save)
- Keyboard handler: listen for `Escape` key globally when in draw mode

#### 4.1.3 Additional chart types
- **Line chart:** `frontend/src/components/LineChart.tsx` — close-only line, no candles, no wicks. Useful for long timeframes.
- **Area chart:** `LineChart.tsx` with a filled area under the line.
- **Heikin Ashi:** variant of `CandlestickChart.tsx` that takes `chartType="heikin_ashi"` and pre-computes HA candles from the raw data.
- **Renko / Kagi / P&F:** out of scope for v4 (see Non-goals) — only if all other items land first and there's demand.
- Add `chart_type` to the `SymbolPage` toolbar with a dropdown

#### 4.1.4 More indicators
- Currently supported: SMA, EMA, MACD, Bollinger (per `CustomIndicatorsPanel.tsx`).
- Add: RSI, VWAP, Ichimoku Cloud, ATR, Stochastic, ADX, OBV, Williams %R, CCI
- File: `frontend/src/components/CustomIndicatorsPanel.tsx` — add the new entries to the dropdown
- For built-in indicators, add a "Built-in" section in the panel that doesn't require the user to configure anything (just toggle on/off)
- For VWAP: needs both price and volume; the bars data already has `volume` populated (per `market_data_sql.py`)

#### 4.1.5 Indicator overlay vs separate pane
- **Overlay (price chart):** SMA, EMA, Bollinger, VWAP, Ichimoku
- **Separate pane below price:** RSI, MACD, Stochastic, ADX, ATR, Williams %R, CCI, OBV
- Add a `pane_height` config to custom indicators; UI shows a small thumbnail of where it renders

#### 4.1.6 Chart settings + drawing persistence
- Chart settings (timeframe, indicator set, chart type): persist per-user via `localStorage` keyed by symbol
- Drawings: already persisted to DB via existing `DrawingTool` API
- Verify the click-to-place flow doesn't break the persistence model — the API should still receive `start_timestamp` / `start_price` from the click handler
- Drawing lock (introduced in 4.1.2) prevents accidental edits

### Before starting — re-verify against current code

This scope was written during Version 3 planning and never built. Before implementing, confirm against the current tree (things may have shifted since):
- `DrawingToolsPanel.tsx`, `CustomIndicatorsPanel.tsx`, `CandlestickChart.tsx` still exist with roughly this shape
- The `DrawingTool` CRUD API (`backend/api/...`) is unchanged
- `lightweight-charts` is still the pinned charting library (`frontend/package.json`)

### Verification
- Click-to-place: open chart, click two points, see trendline appear at correct (timestamp, price)
- Switch to line chart via toolbar dropdown — renders without errors, no wicks
- Toggle RSI on — separate pane appears below price chart
- All chart tests pass; add a new `frontend/src/components/__tests__/chartInteractions.test.ts`

---

## Open questions

1. Re-attempt TradingView at some point, now that lightweight-charts has more mileage on it? Revisit only if lightweight-charts hits a real ceiling — no evidence of that yet.
2. Renko/Kagi/P&F — gauge demand after v1 ships before scoping the data-transformation work.
