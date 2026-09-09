const API_BASE = process.env.REACT_APP_API_BASE_URL || 'http://localhost:5001/api';

// Phase 8: spec-compliant regime names (RISK_ON / RISK_OFF / NEUTRAL / TRANSITION / UNKNOWN)
export type RegimeType = 'risk_on' | 'risk_off' | 'neutral' | 'transition' | 'unknown';

export interface RegimeData {
  symbol: string;
  regime: RegimeType;
  confidence: number;
  strength: number;
  supporting_factors: Record<string, any>;
  timestamp: string | null;
  data_age_seconds: number | null;
  freshness: 'fresh' | 'recent' | 'stale' | 'stuck' | 'unknown';
}

// Phase 8: market-wide context (aggregated from SPY/QQQ/IWM/VIX)
export interface MarketContextData {
  regime: RegimeType;
  confidence: number;
  trend_strength: number;
  momentum: number;
  volatility_state: string;
  sub_regimes: Record<string, RegimeType>;
  contributing_factors: Record<string, any>;
  timestamp: string | null;
}

// Phase 8: relative strength signal vs a benchmark
export interface RelativeStrengthSignal {
  symbol: string;
  benchmark: string;
  rs_pct: number;           // alpha vs benchmark (%)
  classification: 'strong_outperformer' | 'outperformer' | 'inline' | 'underperformer' | 'strong_underperformer' | 'unknown';
  symbol_return_pct: number;
  benchmark_return_pct: number;
  lookback_days: number;
  timestamp: string | null;
}

export interface RelativeStrengthData {
  symbol: string;
  signals: RelativeStrengthSignal[];
  count: number;
}

// Phase 8: sector alignment signal
export interface SectorData {
  symbol: string;
  sector: string;
  sector_etf: string | null;
  stock_trend: string;
  sector_trend: string;
  market_trend: string;
  alignment_score: number;
  alignment_level: string;
  contributing_factors: Record<string, any>;
  timestamp: string | null;
}

export interface TrendData {
  symbol: string;
  timeframe: string;
  direction: string;
  strength: string;
  confidence: number;
  timestamp: string | null;
}

export interface ConfluenceData {
  symbol: string;
  direction: string;
  strength: number;
  alignment_score: number;
  timeframe_signals: Record<string, any>;
  timestamp: string | null;
  // Phase 7: alignment breakdown + horizon directions + preset.
  bullish_alignment?: number;
  bearish_alignment?: number;
  conflicting?: number;
  short_term_direction?: string;
  intermediate_direction?: string;
  higher_direction?: string;
  preset?: string;
}

// Phase 7: per-timeframe trend snapshot (inside MultiTimeframeSnapshot).
export interface TimeframeTrendSnapshot {
  symbol: string;
  timeframe: string;
  timestamp: string | null;
  direction: string;
  score: number;
  strength: number;
  confidence: number;
  data_quality: string;
  strategy_version: string;
}

// Phase 7: multi-timeframe snapshot.
export interface MultiTimeframeSnapshot {
  symbol: string;
  timestamp: string | null;
  preset: string;
  direction: string;
  strength: number;
  alignment_score: number;
  bullish_alignment: number;
  bearish_alignment: number;
  conflicting: number;
  short_term_direction: string;
  intermediate_direction: string;
  higher_direction: string;
  timeframe_snapshots: Record<string, TimeframeTrendSnapshot>;
  strategy_version: string;
}

export interface StrategyData {
  symbol: string;
  strategy_type: string;
  confidence: number;
  timeframe: string;
  parameters: Record<string, any>;
  regime_signal: any;
  trend_signal: any;
  confluence_signal: any;
  timestamp: string | null;
}

export interface HealthData {
  status: string;
  service: string;
  version: string;
}

export interface SystemStatus {
  service: string;
  version: string;
  debug: boolean;
  market_data_provider: string;
  market_data_fallback_providers: string[];
  ai_enabled: boolean;
  timestamp: string;
}

/** Live system configuration read directly from .env — reflects changes
 * without requiring a server restart. Falls back to cached settings for
 * fields that can't be read from the env file. */
export interface SystemConfig {
  service: string;
  version: string;
  market_data_primary_provider: string;
  market_data_fallback_providers: string[];
  ai_enabled: boolean;
  config_source: string;
  timestamp: string;
}

// Phase 3.3.3: WAL mode + Litestream health snapshot.
export interface BackupStatusData {
  timestamp: string;
  journal_mode: string;
  wal_checkpoint_busy: boolean;
  wal_checkpoint_frames: number;
  wal_checkpoint_end: number;
  wal_size_bytes: number;
  shm_size_bytes: number;
  litestream_reachable: boolean;
  litestream_generation: string | null;
  litestream_dbs: any[] | null;
}

export interface Watchlist {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  symbol_count?: number;
  created_at: string;
  updated_at: string;
  symbols?: WatchlistSymbol[];
}

export interface WatchlistSymbol {
  id: number;
  watchlist_id: number;
  symbol: string;
  is_enabled: boolean;
  added_at: string;
  position: number;
  notes?: string | null;
  entity_type?: 'stock' | 'etf' | null;
}

// Alerts
export interface Alert {
  id: number;
  name: string;
  symbol: string;
  condition_type: string;
  parameter: string;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertTrigger {
  id: number;
  alert_id: number;
  symbol: string;
  observed_value: string | null;
  message: string | null;
  triggered_at: string;
}

// Backtest
export type BacktestStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface BacktestRun {
  id: number;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  signals_requested: string;
  strategy_version: string | null;
  status: BacktestStatus;
  total_bars: number | null;
  total_signals: number | null;
  win_rate_1d: number | null;
  avg_return_1d: number | null;
  avg_return_5d: number | null;
  avg_return_20d: number | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

// Analysis — Phase 9
export interface Transition {
  type: string;
  direction: string;
  symbol: string;
  timeframe: string;
  index: number;
  timestamp: string | null;
  previous_score: number;
  current_score: number;
  delta: number;
  magnitude: number;
}

export interface TransitionsResult {
  symbol: string;
  timeframe: string;
  transitions: Transition[];
  count: number;
  latest_score: number;
  latest_timestamp: string | null;
}

export interface SRLevel {
  price: number;
  type: string;
  timeframe: string;
  strength: number;
  touch_count: number;
  age: number | null;
  distance_from_price: number | null;
  origin_index: number | null;
  component_prices: number[];
  timestamp: string | null;
}

export interface SRResult {
  symbol: string;
  timeframe: string;
  levels: SRLevel[];
  count: number;
  latest_close: number | null;
  last_index: number;
}

export interface Divergence {
  type: string;
  direction: string;
  symbol: string;
  timeframe: string;
  pivot_a_index: number;
  pivot_b_index: number;
  pivot_a_price: number;
  pivot_b_price: number;
  pivot_a_indicator: number;
  pivot_b_indicator: number;
  timestamp: string | null;
  strength: number;
}

export interface DivergencesResult {
  symbol: string;
  timeframe: string;
  divergences: Divergence[];
  count: number;
}

export interface Bar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BarsResult {
  symbol: string;
  timeframe: string;
  bars: Bar[];
  count: number;
}

// Scanner — Phase 11 live stream

// Phase 12: top movers / watchlist scan
export interface TopMoverResult {
  symbol: string;
  quote: ScanQuote | null;
  indicator_values: Record<string, any>;
  scores: Record<string, number>;
  total_score: number;
  rank: number | null;
  signals: string[];
  trend_signals: Record<string, any>;
  timestamp: string;
}

export interface WatchlistScanResult {
  symbol: string;
  quote: ScanQuote | null;
  indicator_values: Record<string, any>;
  scores: Record<string, number>;
  total_score: number;
  rank: number | null;
  signals: string[];
  trend_signals: Record<string, any>;
  timestamp: string;
  /** True when the watchlist row is enabled. Defaults to true. */
  is_enabled?: boolean;
  /** Optional notes attached to the symbol in the watchlist. */
  notes?: string | null;
  /** Entity classification: "stock" or "etf". Defaults to "stock". */
  entity_type?: 'stock' | 'etf' | null;
}

export interface WatchlistScanResponse {
  timestamp: string;
  count: number;
  results: WatchlistScanResult[];
}

// Phase 13: historical signal recording
export interface HistoricalSignal {
  id: number;
  symbol: string;
  timestamp: string;
  timeframe: string;
  price: number | null;
  trend_score: number | null;
  trend_state: string | null;
  strength: number | null;
  market_regime: string | null;
  relative_strength: string | null;
  sector_alignment: number | null;
  volume_state: string | null;
  momentum: number | null;
  structure: string | null;
  confidence_inputs: string | null;
  strategy_version: string | null;
  data_quality: string | null;
  return_5b: number | null;
  return_10b: number | null;
  return_20b: number | null;
  mfe: number | null;
  mae: number | null;
  created_at: string | null;
}

export interface RegimePerformance {
  regime: string;
  count: number;
  avg_return_5b: number | null;
  avg_return_10b: number | null;
  avg_return_20b: number | null;
  avg_mfe: number | null;
  avg_mae: number | null;
}

export interface RegimeCount {
  regime: string;
  count: number;
}

export interface ScanQuote {
  symbol: string;
  price: number | null;
  bid: number | null;
  ask: number | null;
  volume: number | null;
  timestamp: string | null;
  provider: string | null;
}

export interface ScanResult {
  symbol: string;
  timestamp: string;
  quote: ScanQuote | null;
  indicator_values: Record<string, any>;
  scores: Record<string, number>;
  total_score: number;
  rank: number | null;
  signals: string[];
  trend_signals: Record<string, any>;
}

// ---- Phase 10: composable filters + named rankings ----

/** A single filter expression — type identifies the filter, params configure it. */
export interface FilterSpec {
  type: string;
  params: Record<string, any>;
}

/** Metadata for a single named ranking category (label + description). */
export interface RankingCategoryMeta {
  name: string;
  label: string;
  description: string;
}

/** A single symbol's rank entry inside a named ranking. */
export interface RankingEntry {
  symbol: string;
  score: number;
  rank: number;
  metrics: Record<string, any>;
}

/** A full named ranking — name + entries + count of eligible symbols. */
export interface NamedRanking {
  name: string;
  label: string;
  description: string;
  total_eligible: number;
  entries: RankingEntry[];
}

// Phase 17: NL search types
export interface NLSearchResultItem {
  symbol: string;
  total_score: number;
  rank: number | null;
  signals: string[];
  trend_directions: Record<string, string>;
  rsi: number | null;
  macd: number | null;
  adx: number | null;
  price: number | null;
}

export interface NLSearchResponse {
  query: string;
  filter_schema: Record<string, any>;
  filter_description: string;
  results: NLSearchResultItem[];
  ranking: string;
  explanation: string | null;
  ai_explanation_used: boolean;
  ai_translation_used: boolean;
  reason: string | null;
  parser_used: string;
  timestamp: string;
}

// Phase 16: AI analysis types
export interface AIAnalysisResult {
  summary: string;
  trend: string;
  confidence: number;
  supporting_factors: string[];
  risk_factors: string[];
  timeframe_conflicts: string[];
  key_levels: string[];
  provider: string;
  model: string;
  is_uncertain: boolean;
  template_id?: number | null;
  template_name?: string | null;
}

export interface AIConfig {
  enabled: boolean;
  provider: string;
  fallback_providers: string[];
  model: string;
  base_url: string;
  timeout: number;
  max_tokens: number;
  temperature: number;
  api_key_set: boolean;
}

export interface AIProviderStatus {
  name: string;
  healthy: boolean;
  is_primary: boolean;
  error: string | null;
}

export type ScannerEvent =
  | { type: 'scan_result'; symbol: string; data: ScanResult }
  | { type: 'scan_error'; symbol: string; error: string }
  | { type: 'subscribed'; symbol: string }
  | { type: 'unsubscribed'; symbol: string }
  | { type: 'pong' }
  | { type: 'error'; message: string };

// --- Phase 2.3.3: realtime bar push ---

export interface BarUpdateData {
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  timestamp: string | null;
}

export type RealtimeEvent =
  | { type: 'bar_update'; symbol: string; timeframe: string; data: BarUpdateData }
  | { type: 'subscribed'; symbol: string; timeframe: string }
  | { type: 'unsubscribed'; symbol: string; timeframe: string }
  | { type: 'pong' }
  | { type: 'error'; message: string };

/** "SYMBOL:TF" subscription key (uppercase symbol, lowercase tf). */
const realtimeKey = (symbol: string, timeframe: string) =>
  `${symbol.toUpperCase()}:${timeframe.toLowerCase()}`;

export class RealtimeSubscriber {
  private ws: WebSocket | null = null;
  private wsUrl: string;
  /** Active (symbol, tf) subscriptions keyed by "SYMBOL:TF". */
  private subs: Set<string> = new Set();
  private listeners: Set<(evt: RealtimeEvent) => void> = new Set();
  private statusListeners: Set<(status: 'connecting' | 'open' | 'closed') => void> = new Set();
  private reconnectAttempts = 0;
  private pingTimer: number | null = null;
  private reconnectTimer: number | null = null;
  private explicitlyClosed = false;
  private currentStatus: 'connecting' | 'open' | 'closed' = 'closed';

  constructor(wsUrl: string) {
    this.wsUrl = wsUrl;
  }

  connect(): void {
    if (this.ws || this.explicitlyClosed) return;
    this.setStatus('connecting');
    try {
      this.ws = new WebSocket(this.wsUrl);
    } catch (e) {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.setStatus('open');
      // Re-subscribe to anything we wanted before a possible reconnect.
      for (const key of Array.from(this.subs)) {
        const [symbol, timeframe] = key.split(':');
        this.send({ action: 'subscribe', symbol, timeframe });
      }
      this.startPing();
    };

    this.ws.onmessage = (msg) => {
      let parsed: RealtimeEvent | null = null;
      try {
        parsed = JSON.parse(msg.data) as RealtimeEvent;
      } catch {
        return;
      }
      for (const l of Array.from(this.listeners)) l(parsed);
    };

    this.ws.onerror = () => { /* onclose handles reconnect */ };
    this.ws.onclose = () => {
      this.stopPing();
      this.ws = null;
      this.setStatus('closed');
      if (!this.explicitlyClosed) this.scheduleReconnect();
    };
  }

  disconnect(): void {
    this.explicitlyClosed = true;
    this.stopPing();
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try { this.ws.close(); } catch { /* ignore */ }
      this.ws = null;
    }
    this.setStatus('closed');
  }

  /** Subscribe to a symbol+timeframe pair. */
  subscribe(symbol: string, timeframe: string): void {
    const key = realtimeKey(symbol, timeframe);
    this.subs.add(key);
    if (this.currentStatus === 'open') {
      const [s, tf] = key.split(':');
      this.send({ action: 'subscribe', symbol: s, timeframe: tf });
    } else {
      this.connect();
    }
  }

  unsubscribe(symbol: string, timeframe: string): void {
    const key = realtimeKey(symbol, timeframe);
    if (this.subs.delete(key) && this.currentStatus === 'open') {
      const [s, tf] = key.split(':');
      this.send({ action: 'unsubscribe', symbol: s, timeframe: tf });
    }
  }

  onEvent(listener: (evt: RealtimeEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: (status: 'connecting' | 'open' | 'closed') => void): () => void {
    this.statusListeners.add(listener);
    listener(this.currentStatus);
    return () => this.statusListeners.delete(listener);
  }

  private send(payload: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = window.setInterval(() => {
      this.send({ action: 'ping' });
    }, 25000);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.explicitlyClosed) return;
    this.setStatus('closed');
    const delay = Math.min(10000, 500 * Math.pow(2, this.reconnectAttempts));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private setStatus(status: 'connecting' | 'open' | 'closed'): void {
    this.currentStatus = status;
    for (const l of Array.from(this.statusListeners)) l(status);
  }
}

/**
 * Lightweight WebSocket client for the scanner stream. One instance per
 * subscriber group — the server tracks per-socket subscriptions, so it's
 * safe to share a socket across multiple symbols.
 *
 * Auto-reconnects on close (with exponential backoff capped at 10s) until
 * ``disconnect()`` is called. Sends a ping every 25s as a keep-alive; the
 * server doesn't time idle sockets but middleboxes often do.
 */
export class ScannerSubscriber {
  private ws: WebSocket | null = null;
  private wsUrl: string;
  private subs: Set<string> = new Set();
  private listeners: Set<(evt: ScannerEvent) => void> = new Set();
  private statusListeners: Set<(status: 'connecting' | 'open' | 'closed') => void> = new Set();
  private reconnectAttempts = 0;
  private pingTimer: number | null = null;
  private reconnectTimer: number | null = null;
  private explicitlyClosed = false;
  private currentStatus: 'connecting' | 'open' | 'closed' = 'closed';

  constructor(wsUrl: string) {
    this.wsUrl = wsUrl;
  }

  /** Open the socket if not already open. Idempotent. */
  connect(): void {
    if (this.ws || this.explicitlyClosed) return;
    this.setStatus('connecting');
    try {
      this.ws = new WebSocket(this.wsUrl);
    } catch (e) {
      // Browser refused to even construct the socket — schedule a retry
      // so transient errors (offline, etc.) don't kill the page.
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.setStatus('open');
      // Re-subscribe to anything we wanted before a possible reconnect.
      for (const sym of Array.from(this.subs)) {
        this.send({ action: 'subscribe', symbol: sym });
      }
      this.startPing();
    };

    this.ws.onmessage = (msg) => {
      let parsed: ScannerEvent | null = null;
      try {
        parsed = JSON.parse(msg.data) as ScannerEvent;
      } catch {
        return; // Ignore non-JSON frames silently.
      }
      for (const l of Array.from(this.listeners)) l(parsed);
    };

    this.ws.onerror = () => {
      // onclose will fire right after; reconnect there.
    };

    this.ws.onclose = () => {
      this.stopPing();
      this.ws = null;
      this.setStatus('closed');
      if (!this.explicitlyClosed) this.scheduleReconnect();
    };
  }

  /** Close the socket and stop reconnecting. */
  disconnect(): void {
    this.explicitlyClosed = true;
    this.stopPing();
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try { this.ws.close(); } catch { /* ignore */ }
      this.ws = null;
    }
    this.setStatus('closed');
  }

  /** Add a symbol subscription. Opens the socket if needed. */
  subscribe(symbol: string): void {
    const upper = symbol.toUpperCase();
    this.subs.add(upper);
    if (this.currentStatus === 'open') {
      this.send({ action: 'subscribe', symbol: upper });
    } else {
      this.connect();
    }
  }

  /** Remove a symbol subscription. */
  unsubscribe(symbol: string): void {
    const upper = symbol.toUpperCase();
    if (this.subs.delete(upper) && this.currentStatus === 'open') {
      this.send({ action: 'unsubscribe', symbol: upper });
    }
  }

  onEvent(listener: (evt: ScannerEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: (status: 'connecting' | 'open' | 'closed') => void): () => void {
    this.statusListeners.add(listener);
    listener(this.currentStatus);
    return () => this.statusListeners.delete(listener);
  }

  private send(payload: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = window.setInterval(() => {
      this.send({ action: 'ping' });
    }, 25000);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.explicitlyClosed) return;
    this.setStatus('closed');
    // Exponential backoff: 0.5s, 1s, 2s, 4s, 8s, 10s (cap).
    const delay = Math.min(10000, 500 * Math.pow(2, this.reconnectAttempts));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private setStatus(status: 'connecting' | 'open' | 'closed'): void {
    this.currentStatus = status;
    for (const l of Array.from(this.statusListeners)) l(status);
  }
}

export interface BacktestTrade {
  id: number;
  run_id: number;
  signal: string;
  entry_date: string;
  entry_price: number;
  exit_date_1d: string | null;
  exit_price_1d: number | null;
  return_1d: number | null;
  exit_date_5d: string | null;
  exit_price_5d: number | null;
  return_5d: number | null;
  exit_date_20d: string | null;
  exit_price_20d: number | null;
  return_20d: number | null;
}

// ---- Phase 18: auxiliary data ----

export interface NewsItem {
  headline: string;
  source: string;
  timestamp: string;
  symbol: string;
  relevance: number;
}

// ---- Phase 2.3.4: custom indicators ----

export interface CustomIndicator {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  formula_type: string;
  parameters: Record<string, any>;
  color: string | null;
  line_width: number | null;
  line_style: string | null;
  separate_pane: boolean;
  pane_height: number | null;
  is_overlay: boolean;
  z_index: number;
  is_active: boolean;
  watchlist_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface IndicatorValue {
  timestamp: string;
  value: number;
}

export interface IndicatorValuesResult {
  indicator_id: number;
  symbol: string;
  timeframe: string;
  count: number;
  values: IndicatorValue[];
}

// ---- Phase 2.3.5: drawing tools ----

export type DrawingType =
  | 'trend_line'
  | 'horizontal_line'
  | 'fib_retracement'
  | 'rectangle'
  | 'arrow'
  | 'text'
  | 'channel'
  | 'pitchfork'
  | 'gann_fan';

export interface DrawingTool {
  id: number;
  watchlist_id: number | null;
  symbol: string;
  timeframe: string;
  drawing_type: DrawingType;
  label: string | null;
  color: string | null;
  line_width: number | null;
  line_style: string | null;
  font_size: number | null;
  opacity: number | null;
  start_timestamp: string;
  start_price: number;
  end_timestamp: string | null;
  end_price: number | null;
  fib_levels: string | null;
  top_price: number | null;
  bottom_price: number | null;
  is_visible: boolean;
  is_locked: boolean;
  extend_left: boolean;
  extend_right: boolean;
  created_at: string;
  updated_at: string;
}

export interface NewsResponse {
  symbol: string;
  items: NewsItem[];
  provider: string;
  timestamp: string;
}

export interface FundamentalsItem {
  symbol: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  shares_outstanding: number | null;
  revenue: number | null;
  net_income: number | null;
  eps: number | null;
  eps_growth: number | null;
  pe_ratio: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  price_to_book: number | null;
  price_to_sales: number | null;
  total_debt: number | null;
  total_cash: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  dividend_yield: number | null;
  payout_ratio: number | null;
  institutional_ownership: number | null;
  insider_ownership: number | null;
  short_float: number | null;
  analyst_target: number | null;
  recommendation: string | null;
  beta: number | null;
  week_52_high: number | null;
  week_52_low: number | null;
}

export interface FundamentalsResponse {
  symbol: string;
  data: FundamentalsItem;
  provider: string;
  timestamp: string;
}

export type OptionsType = 'call' | 'put';
export type UnusualActivity = 'normal' | 'elevated' | 'high' | 'unusual';

export interface OptionContract {
  strike: number;
  expiration: string;
  option_type: OptionsType;
  bid: number | null;
  ask: number | null;
  last: number | null;
  volume: number | null;
  open_interest: number | null;
  implied_volatility: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  rho: number | null;
  in_the_money: boolean;
}

export interface OptionsChain {
  symbol: string;
  expiration: string;
  calls: OptionContract[];
  puts: OptionContract[];
  put_call_ratio: number | null;
  total_call_volume: number | null;
  total_put_volume: number | null;
  avg_iv_call: number | null;
  avg_iv_put: number | null;
  unusual_activity: UnusualActivity;
}

export interface OptionsResponse {
  symbol: string;
  chains: OptionsChain[];
  expirations: string[];
  near_term_iv: number | null;
  iv_rank: number | null;
  provider: string;
  timestamp: string;
}

class ApiService {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl;
  }

  /**
   * Compute the WebSocket URL for the scanner stream from the HTTP API base.
   * ``http://host:5001/api`` becomes ``ws://host:5001/api/scanner-stream/ws``
   * (and ``https`` becomes ``wss``). Exposed for components that want to
   * manage their own socket lifecycle.
   */
  getScannerWsUrl(): string {
    const httpBase = this.baseUrl.replace(/\/+$/, '');
    const wsBase = httpBase.replace(/^http/, 'ws');
    return `${wsBase}/scanner-stream/ws`;
  }

  /**
   * Compute the WebSocket URL for the realtime bar push stream from the
   * HTTP API base. ``http://host:5001/api`` becomes
   * ``ws://host:5001/api/realtime/ws`` (and ``https`` becomes ``wss``).
   */
  getRealtimeWsUrl(): string {
    const httpBase = this.baseUrl.replace(/\/+$/, '');
    const wsBase = httpBase.replace(/^http/, 'ws');
    return `${wsBase}/realtime/ws`;
  }

  private async fetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Same as ``fetch`` but returns the raw text body instead of parsing JSON.
   * Used by the watchlist export endpoint, which can return either JSON
   * or CSV depending on the ``format`` query param.
   */
  private async fetchRaw(endpoint: string, options?: RequestInit): Promise<string> {
    const response = await fetch(`${this.baseUrl}${endpoint}`, options);
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    return response.text();
  }

  /**
   * DELETE returns 204 No Content. ``fetch<T>`` always calls
   * ``response.json()`` which would throw on 204, so deletes go
   * through this helper instead.
   */
  private async del(endpoint: string, options?: RequestInit): Promise<void> {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
  }

  // Health & System
  async getHealth(): Promise<HealthData> {
    return this.fetch<HealthData>('/health');
  }

  async getSystemStatus(): Promise<SystemStatus> {
    return this.fetch<SystemStatus>('/system/status');
  }

  /** Live config read from .env — reflects runtime changes without restart. */
  async getSystemConfig(): Promise<SystemConfig> {
    return this.fetch<SystemConfig>('/system/config');
  }

  // Phase 3.3.3: WAL + Litestream backup health.
  async getBackupStatus(): Promise<BackupStatusData> {
    return this.fetch<BackupStatusData>('/system/backup-status');
  }

  // Market Regime
  async getRegime(symbol: string): Promise<RegimeData> {
    return this.fetch<RegimeData>(`/regime/${symbol}/current`);
  }

  async getRegimeHistory(symbol: string, limit: number = 10): Promise<{ symbol: string; history: RegimeData[]; count: number }> {
    return this.fetch(`/regime/${symbol}/history?limit=${limit}`);
  }

  async updateRegime(symbol: string, price: number, volume: number, high?: number, low?: number): Promise<any> {
    return this.fetch(`/regime/${symbol}/update`, {
      method: 'POST',
      body: JSON.stringify({ price, volume, high, low }),
    });
  }

  // Phase 8: relative strength vs benchmarks
  async getRelativeStrength(symbol: string): Promise<RelativeStrengthData> {
    return this.fetch<RelativeStrengthData>(`/regime/${symbol}/relative-strength`);
  }

  // Phase 3.9.12: batch relative-strength (N symbols in one HTTP round-trip)
  async getBatchRelativeStrength(
    symbols: string[],
  ): Promise<{ results: Record<string, RelativeStrengthData>; count: number }> {
    return this.fetch(`/regime/batch/relative-strength?symbols=${symbols.map(s => s.toUpperCase()).join(',')}`);
  }

  // Phase 8: sector alignment signal
  async getSector(symbol: string): Promise<SectorData> {
    return this.fetch<SectorData>(`/regime/${symbol}/sector`);
  }

  // Phase 8: market-wide context (aggregate of SPY/QQQ/IWM/VIX)
  async getMarketContext(): Promise<MarketContextData> {
    return this.fetch<MarketContextData>('/market-context/current');
  }

  async getMarketContextHistory(limit = 100): Promise<{ history: MarketContextData[]; count: number }> {
    return this.fetch<{ history: MarketContextData[]; count: number }>(`/market-context/history?limit=${limit}`);
  }

  // Trend Analysis
  async getTrend(symbol: string, timeframe: string): Promise<TrendData> {
    return this.fetch<TrendData>(`/trend/${symbol}/current/${timeframe}`);
  }

  async getTrends(symbol: string, timeframes: string[] = ['1h', '4h', '1d']): Promise<TrendData[]> {
    const results = await Promise.all(
      timeframes.map(tf => this.getTrend(symbol, tf).catch(() => null))
    );
    return results.filter((r): r is TrendData => r !== null);
  }

  async getTrendHistory(symbol: string, timeframe: string, limit: number = 10): Promise<any> {
    return this.fetch(`/trend/${symbol}/history/${timeframe}?limit=${limit}`);
  }

  // Multi-Timeframe
  async getConfluence(symbol: string, preset: string = 'day_trading'): Promise<ConfluenceData> {
    return this.fetch<ConfluenceData>(`/multitimeframe/${symbol}/confluence?preset=${encodeURIComponent(preset)}`);
  }

  async getConfluenceHistory(symbol: string, limit: number = 10, preset: string = 'day_trading'): Promise<any> {
    return this.fetch(`/multitimeframe/${symbol}/history?limit=${limit}&preset=${encodeURIComponent(preset)}`);
  }

  async getMTFPresets(): Promise<{ presets: Array<{ name: string }> }> {
    return this.fetch(`/multitimeframe/presets`);
  }

  // Phase 7: multi-timeframe snapshot.
  async getMTFSnapshot(symbol: string, preset: string = 'day_trading'): Promise<{ symbol: string; snapshot: MultiTimeframeSnapshot | null }> {
    return this.fetch(`/multitimeframe/${symbol}/snapshot?preset=${encodeURIComponent(preset)}`);
  }

  async getMTFSnapshotHistory(symbol: string, limit: number = 100, preset: string = 'day_trading'): Promise<{ symbol: string; history: MultiTimeframeSnapshot[]; count: number }> {
    return this.fetch(`/multitimeframe/${symbol}/snapshot/history?limit=${limit}&preset=${encodeURIComponent(preset)}`);
  }

  // Strategy
  async getStrategy(symbol: string): Promise<StrategyData> {
    return this.fetch<StrategyData>(`/strategy/${symbol}/current`);
  }

  async getStrategyHistory(symbol: string, limit: number = 10): Promise<any> {
    return this.fetch(`/strategy/${symbol}/history?limit=${limit}`);
  }

  // Watchlists (backend uses /api/watchlists prefix - plural)
  async getWatchlists(): Promise<Watchlist[]> {
    return this.fetch<Watchlist[]>('/watchlists/');
  }

  async getWatchlist(id: number): Promise<Watchlist> {
    return this.fetch<Watchlist>(`/watchlists/${id}`);
  }

  async getWatchlistSymbols(watchlistId: number): Promise<WatchlistSymbol[]> {
    return this.fetch<WatchlistSymbol[]>(`/watchlists/${watchlistId}/symbols`);
  }

  async createWatchlist(name: string, description?: string): Promise<Watchlist> {
    return this.fetch<Watchlist>('/watchlists/', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    });
  }

  async updateWatchlist(id: number, data: Partial<Watchlist>): Promise<Watchlist> {
    return this.fetch<Watchlist>(`/watchlists/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteWatchlist(id: number): Promise<void> {
    return this.del(`/watchlists/${id}`);
  }

  async addSymbolToWatchlist(watchlistId: number, symbol: string, entityType: 'stock' | 'etf' = 'stock'): Promise<WatchlistSymbol> {
    // The backend now registers the symbol for live tracking and enqueues
    // its backfill synchronously as part of this request (see
    // backend/api/watchlist/router.py's add_symbol_to_watchlist) — no
    // follow-up call needed for the ingestion service to pick it up. This
    // used to be a required (but silently-failable, since its error was
    // swallowed) second call; it no longer is, so it's gone.
    return this.fetch<WatchlistSymbol>(`/watchlists/${watchlistId}/symbols`, {
      method: 'POST',
      body: JSON.stringify({ symbol, entity_type: entityType }),
    });
  }

  async removeSymbolFromWatchlist(watchlistId: number, symbol: string): Promise<void> {
    await this.del(`/watchlists/${watchlistId}/symbols/${symbol}`);
    // The backend already syncs the ingestion service in-process,
    // synchronously, as part of this DELETE (unlike add, this path was
    // always correct without a follow-up call) — kept as a harmless,
    // redundant nudge rather than reworking a path that wasn't broken.
    this.refreshIngestionSymbols().catch(() => {/* non-fatal */});
  }

  async refreshIngestionSymbols(): Promise<{ message: string; symbols: string[] }> {
    return this.fetch('/market-data/ingestion/symbols/refresh', { method: 'POST' });
  }

  async getBackfillStatus(symbol: string): Promise<{
    symbol: string;
    job_id: string;
    status: 'queued' | 'started' | 'completed' | 'partial' | 'failed';
    tier1_written: number;
    tier2_written: number;
    tier3_written: number;
    gaps_found: number;
    gaps_filled: number;
    result: Record<string, unknown> | null;
    error: string | null;
    created_at: string | null;
    started_at: string | null;
    completed_at: string | null;
  }> {
    return this.fetch(`/watchlists/symbols/${symbol}/backfill-status`);
  }

  async reorderSymbols(watchlistId: number, symbols: string[]): Promise<void> {
    return this.fetch(`/watchlists/${watchlistId}/symbols/reorder`, {
      method: 'PUT',
      body: JSON.stringify(symbols),
    });
  }

  async enableSymbol(watchlistId: number, symbol: string): Promise<WatchlistSymbol> {
    return this.fetch<WatchlistSymbol>(
      `/watchlists/${watchlistId}/symbols/${symbol}/enable`,
      { method: 'PUT' },
    );
  }

  async disableSymbol(watchlistId: number, symbol: string): Promise<WatchlistSymbol> {
    return this.fetch<WatchlistSymbol>(
      `/watchlists/${watchlistId}/symbols/${symbol}/disable`,
      { method: 'PUT' },
    );
  }

  async updateWatchlistSymbol(
    watchlistId: number,
    symbol: string,
    updates: { notes?: string | null; is_enabled?: boolean },
  ): Promise<WatchlistSymbol> {
    return this.fetch<WatchlistSymbol>(
      `/watchlists/${watchlistId}/symbols/${symbol}`,
      { method: 'PATCH', body: JSON.stringify(updates) },
    );
  }

  async searchWatchlistSymbols(
    watchlistId: number,
    query: string,
  ): Promise<WatchlistSymbol[]> {
    return this.fetch<WatchlistSymbol[]>(
      `/watchlists/${watchlistId}/symbols/search?q=${encodeURIComponent(query)}`,
    );
  }

  async importWatchlist(
    watchlistId: number,
    symbols: string[],
  ): Promise<{ imported: string[]; skipped: string[]; errors: string[] }> {
    return this.fetch(`/watchlists/${watchlistId}/import`, {
      method: 'POST',
      body: JSON.stringify({ symbols }),
    });
  }

  async exportWatchlist(
    watchlistId: number,
    format: 'json' | 'csv' = 'json',
  ): Promise<string> {
    return this.fetchRaw(`/watchlists/${watchlistId}/export?format=${format}`);
  }

  // Alerts
  async getAlerts(): Promise<Alert[]> {
    return this.fetch<Alert[]>('/alerts/');
  }

  async createAlert(payload: {
    name: string;
    symbol: string;
    condition_type: string;
    parameter: string;
  }): Promise<Alert> {
    return this.fetch<Alert>('/alerts/', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateAlert(id: number, payload: Partial<Alert>): Promise<Alert> {
    return this.fetch<Alert>(`/alerts/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteAlert(id: number): Promise<void> {
    return this.del(`/alerts/${id}`);
  }

  async getAlertTriggers(alertId: number, limit = 100): Promise<AlertTrigger[]> {
    return this.fetch<AlertTrigger[]>(`/alerts/${alertId}/triggers?limit=${limit}`);
  }

  async getActiveAlertTriggers(): Promise<AlertTrigger[]> {
    return this.fetch<AlertTrigger[]>('/alerts/active');
  }

  // Backtest
  async getBacktestRuns(limit = 20): Promise<BacktestRun[]> {
    return this.fetch<BacktestRun[]>(`/backtest/?limit=${limit}`);
  }

  async createBacktest(payload: {
    symbol: string;
    start_date: string;
    end_date: string;
    signals?: string[];
    strategy_version?: string;
  }): Promise<BacktestRun> {
    return this.fetch<BacktestRun>('/backtest/', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getBacktestRun(id: number): Promise<BacktestRun> {
    return this.fetch<BacktestRun>(`/backtest/${id}`);
  }

  async getBacktestTrades(id: number): Promise<BacktestTrade[]> {
    return this.fetch<BacktestTrade[]>(`/backtest/${id}/trades`);
  }

  async deleteBacktestRun(id: number): Promise<void> {
    return this.del(`/backtest/${id}`);
  }

  // Market Data
  async getQuote(symbol: string): Promise<any> {
    return this.fetch(`/market-data/quote/${symbol}`);
  }

  async getLatestBars(symbol: string): Promise<Record<string, any>> {
    return this.fetch(`/market-data/bars/${symbol}`);
  }

  async getIngestionStatus(): Promise<any> {
    return this.fetch('/market-data/ingestion/status');
  }

  async toggleIngestion(): Promise<{ is_running: boolean; message: string }> {
    return this.fetch('/market-data/ingestion/toggle', { method: 'POST' });
  }

  // Analysis (Phase 9)
  async getTransitions(symbol: string, timeframe = '1d', window = 5, minDelta = 10): Promise<TransitionsResult> {
    return this.fetch<TransitionsResult>(
      `/analysis/${symbol}/transitions?timeframe=${timeframe}&window=${window}&min_delta=${minDelta}`
    );
  }

  async getSupportResistance(symbol: string, timeframe = '1d', limit = 500): Promise<SRResult> {
    return this.fetch<SRResult>(
      `/analysis/${symbol}/support-resistance?timeframe=${timeframe}&limit=${limit}`
    );
  }

  async getDivergences(symbol: string, timeframe = '1d', limit = 200): Promise<DivergencesResult> {
    return this.fetch<DivergencesResult>(
      `/analysis/${symbol}/divergences?timeframe=${timeframe}&limit=${limit}`
    );
  }

  async getAnalysisBars(symbol: string, timeframe = '1d', limit = 60): Promise<BarsResult> {
    return this.fetch<BarsResult>(
      `/analysis/${symbol}/bars?timeframe=${timeframe}&limit=${limit}`
    );
  }

  // Phase 13: historical signal recording
  async listSignals(
    symbol?: string,
    timeframe?: string,
    limit = 100,
    completedOnly = false,
  ): Promise<HistoricalSignal[]> {
    const params = new URLSearchParams();
    if (symbol) params.set('symbol', symbol);
    if (timeframe) params.set('timeframe', timeframe);
    params.set('limit', String(limit));
    if (completedOnly) params.set('completed_only', 'true');
    return this.fetch<HistoricalSignal[]>(`/signals/?${params.toString()}`);
  }

  async getLatestSignalsForSymbol(symbol: string): Promise<Record<string, HistoricalSignal>> {
    return this.fetch<Record<string, HistoricalSignal>>(
      `/signals/symbol/${encodeURIComponent(symbol)}/latest`,
    );
  }

  async getRegimePerformance(): Promise<RegimePerformance[]> {
    return this.fetch<RegimePerformance[]>('/signals/research/regime-performance');
  }

  async getSignalCountByRegime(): Promise<RegimeCount[]> {
    return this.fetch<RegimeCount[]>('/signals/research/count-by-regime');
  }

  async backfillOutcomes(batchSize = 50): Promise<{ updated: number }> {
    return this.fetch<{ updated: number }>(
      `/signals/backfill?batch_size=${batchSize}`,
      { method: 'POST' },
    );
  }

  async recordSignalsNow(
    symbols?: string[],
    timeframes?: string[],
  ): Promise<{ recorded: number }> {
    return this.fetch<{ recorded: number }>('/signals/record', {
      method: 'POST',
      body: JSON.stringify({ symbols, timeframes }),
    });
  }

  async deleteOldSignals(days = 180): Promise<{ deleted: number; older_than_days: number }> {
    return this.fetch<{ deleted: number; older_than_days: number }>(
      `/signals/old?days=${days}`,
      { method: 'DELETE' },
    );
  }

  // Scanner (Phase 11 — live stream)
  // Returns a fresh subscriber bound to this ApiService's WS URL. The
  // caller is responsible for calling ``disconnect()`` on unmount.
  createScannerSubscriber(): ScannerSubscriber {
    return new ScannerSubscriber(this.getScannerWsUrl());
  }

  // Phase 2.3.3: realtime bar push
  createRealtimeSubscriber(): RealtimeSubscriber {
    return new RealtimeSubscriber(this.getRealtimeWsUrl());
  }

  // Phase 12: top movers (bullish/bearish)
  async getTopMovers(
    direction: 'bullish' | 'bearish',
    limit = 10,
    watchlistId?: number,
  ): Promise<TopMoverResult[]> {
    const params = new URLSearchParams({ direction, limit: String(limit) });
    if (watchlistId != null) params.set('watchlist_id', String(watchlistId));
    return this.fetch<TopMoverResult[]>(`/scanner/top-movers?${params}`);
  }

  // Phase 10: composable filters + named rankings
  async getFilterTypes(): Promise<string[]> {
    return this.fetch<string[]>('/scanner/filter-types');
  }

  async applyFilter(
    body: { filters: FilterSpec[]; match: 'AND' | 'OR' },
    symbols?: string[],
  ): Promise<ScanResult[]> {
    const qs = symbols && symbols.length
      ? `?symbols=${symbols.map(s => s.toUpperCase()).join(',')}`
      : '';
    return this.fetch<ScanResult[]>(`/scanner/filter${qs}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async getRankings(
    body: { filters: FilterSpec[]; match: 'AND' | 'OR' },
    topN = 10,
    symbols?: string[],
  ): Promise<NamedRanking[]> {
    const params = new URLSearchParams({ top_n: String(topN) });
    if (symbols && symbols.length) {
      params.set('symbols', symbols.map(s => s.toUpperCase()).join(','));
    }
    return this.fetch<NamedRanking[]>(`/scanner/rankings?${params}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async getRankingCategories(): Promise<RankingCategoryMeta[]> {
    return this.fetch<RankingCategoryMeta[]>('/scanner/rankings/categories');
  }

  // Phase 12: scan an entire watchlist (for the watchlist table)
  async getWatchlistScan(watchlistId: number): Promise<WatchlistScanResponse> {
    return this.fetch<WatchlistScanResponse>(`/scanner/watchlist/${watchlistId}`);
  }

  // Phase 12: scan a single symbol (for SymbolPage MTF grid + score panel)
  async getScanResult(symbol: string): Promise<ScanResult> {
    return this.fetch<ScanResult>(`/scanner/${encodeURIComponent(symbol)}`);
  }

  // Phase 17: Natural-language search
  async nlSearch(payload: {
    query: string;
    explain?: boolean;
    top_n?: number;
    watchlist_id?: number | null;
    scope?: 'watchlist' | 'market';
    ranking?: string;
  }): Promise<NLSearchResponse> {
    return this.fetch<NLSearchResponse>('/nl-search', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  // Phase 16: AI symbol analysis (extended in Phase 2.4.5 for template_id)
  async analyzeSymbol(
    symbol: string,
    timeframe: string = '1d',
    options?: {
      max_tokens?: number;
      temperature?: number;
      template_id?: number;
    },
  ): Promise<AIAnalysisResult> {
    const params = new URLSearchParams({ symbol, timeframe });
    if (options?.max_tokens) params.set('max_tokens', String(options.max_tokens));
    if (options?.temperature != null) params.set('temperature', String(options.temperature));
    if (options?.template_id != null) params.set('template_id', String(options.template_id));
    // The endpoint is POST-only (backend/api/ai/router.py) — this.fetch()
    // defaults to GET when no method is given, which 405s. Found live
    // 2026-09-09 clicking "Re-run" in AIAnalysisPanel with AI actually
    // enabled for the first time.
    return this.fetch<AIAnalysisResult>(`/ai/analyze?${params}`, { method: 'POST' });
  }

  // Phase 16: AI provider config
  async getAIConfig(): Promise<AIConfig> {
    return this.fetch<AIConfig>('/ai/config');
  }

  // Phase 16: toggle AI enabled/disabled at runtime
  async setAIEnabled(enabled: boolean): Promise<AIConfig> {
    return this.fetch<AIConfig>('/ai/config', {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
  }

  // Phase 16: AI provider status
  async getAIStatus(): Promise<AIProviderStatus[]> {
    return this.fetch<AIProviderStatus[]>('/ai/status');
  }

  // ---- Phase 18: auxiliary data (news / fundamentals / options) ----

  async getNews(symbol: string, limit = 20): Promise<NewsResponse> {
    return this.fetch<NewsResponse>(`/aux-data/news/${symbol}?limit=${limit}`);
  }

  async getFundamentals(symbol: string): Promise<FundamentalsResponse> {
    return this.fetch<FundamentalsResponse>(`/aux-data/fundamentals/${symbol}`);
  }

  async getOptions(symbol: string, expiration?: string): Promise<OptionsResponse> {
    const params = expiration ? `?expiration=${encodeURIComponent(expiration)}` : '';
    return this.fetch<OptionsResponse>(`/aux-data/options/${symbol}${params}`);
  }

  // ── Phase 2.3.4: custom indicators ──────────────────────────────────

  async getCustomIndicators(watchlistId?: number): Promise<CustomIndicator[]> {
    const params = watchlistId != null ? `?watchlist_id=${watchlistId}` : '';
    return this.fetch<CustomIndicator[]>(`/custom-indicators${params}`);
  }

  async createCustomIndicator(data: Partial<CustomIndicator>): Promise<CustomIndicator> {
    return this.fetch<CustomIndicator>('/custom-indicators', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async updateCustomIndicator(id: number, data: Partial<CustomIndicator>): Promise<CustomIndicator> {
    return this.fetch<CustomIndicator>(`/custom-indicators/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async deleteCustomIndicator(id: number): Promise<void> {
    return this.fetch<void>(`/custom-indicators/${id}`, { method: 'DELETE' });
  }

  async computeCustomIndicator(
    id: number,
    symbol: string,
    timeframe: string,
    limit = 200,
  ): Promise<IndicatorValuesResult> {
    return this.fetch<IndicatorValuesResult>(
      `/custom-indicators/compute/${id}?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`,
      { method: 'POST' },
    );
  }

  // ── Phase 2.3.5: drawing tools ──────────────────────────────────────

  async getDrawingTools(params?: {
    symbol?: string;
    timeframe?: string;
    watchlistId?: number;
    drawingType?: string;
  }): Promise<DrawingTool[]> {
    const q = new URLSearchParams();
    if (params?.symbol) q.set('symbol', params.symbol);
    if (params?.timeframe) q.set('timeframe', params.timeframe);
    if (params?.watchlistId != null) q.set('watchlist_id', String(params.watchlistId));
    if (params?.drawingType) q.set('drawing_type', params.drawingType);
    const qs = q.toString();
    return this.fetch<DrawingTool[]>(`/drawing-tools${qs ? `?${qs}` : ''}`);
  }

  async createDrawingTool(data: Partial<DrawingTool>): Promise<DrawingTool> {
    return this.fetch<DrawingTool>('/drawing-tools', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async updateDrawingTool(id: number, data: Partial<DrawingTool>): Promise<DrawingTool> {
    return this.fetch<DrawingTool>(`/drawing-tools/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async deleteDrawingTool(id: number): Promise<void> {
    return this.fetch<void>(`/drawing-tools/${id}`, { method: 'DELETE' });
  }

  // ── Phase 2.4.5: AI templates ─────────────────────────────────────

  async getAITemplates(activeOnly = false): Promise<AITemplate[]> {
    const qs = activeOnly ? '?active_only=true' : '';
    return this.fetch<AITemplate[]>(`/ai/templates${qs}`);
  }

  async getAITemplate(id: number): Promise<AITemplate> {
    return this.fetch<AITemplate>(`/ai/templates/${id}`);
  }

  async getAIDefaultTemplate(): Promise<AITemplate> {
    return this.fetch<AITemplate>('/ai/templates/default');
  }

  async createAITemplate(data: {
    name: string;
    description?: string;
    system_prompt: string;
    user_instructions?: string;
    variables?: string[];
    is_active?: boolean;
    is_default?: boolean;
  }): Promise<AITemplate> {
    return this.fetch<AITemplate>('/ai/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async updateAITemplate(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      system_prompt: string;
      user_instructions: string;
      variables: string[];
      is_active: boolean;
      is_default: boolean;
    }>,
  ): Promise<AITemplate> {
    return this.fetch<AITemplate>(`/ai/templates/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async deleteAITemplate(id: number): Promise<void> {
    return this.del(`/ai/templates/${id}`);
  }

  async previewAITemplate(
    id: number,
    symbol: string,
    timeframe: string,
  ): Promise<AITemplatePreview> {
    return this.fetch<AITemplatePreview>(
      `/ai/templates/${id}/preview?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`,
    );
  }

  // ── Phase 2.5: background AI jobs (RQ) ──────────────────────────────

  async enqueueAIJob(payload: {
    symbol: string;
    timeframe?: string;
    template_id?: number | null;
  }): Promise<AIJobEnqueueResponse> {
    return this.fetch<AIJobEnqueueResponse>('/ai/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  async getAIJob(jobId: string): Promise<AIJobStatusResponse> {
    return this.fetch<AIJobStatusResponse>(`/ai/jobs/${encodeURIComponent(jobId)}`);
  }
}

// ── Phase 2.4.5: AI template types ──────────────────────────────────────

export interface AITemplate {
  id: number;
  name: string;
  description: string | null;
  system_prompt: string;
  user_instructions: string | null;
  variables: string[];
  is_active: boolean;
  is_default: boolean;
  is_system: boolean;
  created_at: string;
  updated_at: string;
}

export interface AITemplatePreview {
  template_id: number;
  system_prompt_rendered: string;
  variables_used: Record<string, string>;
  missing_variables: string[];
}

// ── Phase 2.5: background AI job types ────────────────────────────────────

export interface AIJobEnqueueResponse {
  job_id: string;
  status: string;
  symbol: string;
  timeframe: string;
  template_id?: number | null;
  template_name?: string | null;
}

export interface AIJobStatusResponse {
  job_id: string;
  status: 'queued' | 'started' | 'finished' | 'failed';
  symbol: string;
  timeframe: string;
  template_id?: number | null;
  template_name?: string | null;
  result: AIAnalysisResult | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export const api = new ApiService();
export default api;
