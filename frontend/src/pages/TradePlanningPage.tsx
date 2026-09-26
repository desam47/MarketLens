import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api, {
  PriceCandidate,
  TradeDirection,
  TradePlanDraft,
} from '../services/api';
import type { NavigationState } from '../utils/appNavigation';
import { TIMEFRAMES, TIMEFRAME_LABELS, DEFAULT_TIMEFRAME } from '../utils/timeframeUtils';
import { SymbolAutocompleteInput, resolveWatchlistSymbol } from '../components/SymbolAutocompleteInput';

const STORAGE_KEY = 'marketlens.trade.plans';

/** Saved plans plus the risk inputs, which are worth remembering between visits. */
interface PlanningStore {
  accountValue: number | null;
  riskPercent: number | null;
  plans: SavedPlan[];
}

/** Field names mirror JournalEntry so a plan can later be promoted into the
 * journal without a migration. */
interface SavedPlan {
  id: string;
  symbol: string;
  side: TradeDirection;
  timeframe: string;
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  quantity: number;
  stopSource: string;
  rewardRisk: number | null;
  thesis: string;
  createdAt: string;
}

const EMPTY_STORE: PlanningStore = { accountValue: null, riskPercent: 1, plans: [] };

function readStore(): PlanningStore {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_STORE;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return EMPTY_STORE;
    return {
      accountValue: Number.isFinite(parsed.accountValue) ? parsed.accountValue : null,
      riskPercent: Number.isFinite(parsed.riskPercent) ? parsed.riskPercent : 1,
      plans: Array.isArray(parsed.plans)
        ? parsed.plans.filter(
            (p: any) => p && typeof p.id === 'string' && typeof p.symbol === 'string',
          )
        : [],
    };
  } catch {
    return EMPTY_STORE;
  }
}

function money(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function pct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value.toFixed(digits)}%`;
}

function formatAsOf(timestamp: string | null | undefined): string {
  if (!timestamp) return 'timestamp unavailable';
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? 'timestamp unavailable' : date.toLocaleString();
}

const SOURCE_LABELS: Record<string, string> = {
  structural: 'Structural',
  volatility: 'Volatility (ATR)',
  supertrend: 'Supertrend flip',
  empirical: 'Empirical',
  options_implied: 'Options-implied',
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] || source.replace(/_/g, ' ');
}

/** Reward:risk of a candidate target against the selected stop. */
function rewardRisk(target: number, entry: number, stop: number): number | null {
  const risk = Math.abs(entry - stop);
  if (!risk) return null;
  return Math.abs(target - entry) / risk;
}

function CandidateTable({
  title,
  candidates,
  entry,
  selectedStop,
  selectedTargetPrices,
  emptyNote,
}: {
  title: string;
  candidates: PriceCandidate[];
  entry: number | null;
  selectedStop: number | null;
  selectedTargetPrices?: number[];
  emptyNote: string;
}) {
  if (candidates.length === 0) {
    return (
      <div className="card">
        <h3>{title}</h3>
        <p className="empty-state">{emptyNote}</p>
      </div>
    );
  }
  const showRR = title.toLowerCase().includes('target') && entry != null && selectedStop != null;
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="risk-table-wrap">
        <table className="risk-table">
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">Price</th>
              <th scope="col">Distance</th>
              {showRR && <th scope="col">R:R</th>}
              <th scope="col">Rationale</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate, index) => {
              const isSelectedStop = selectedStop != null && candidate.price === selectedStop;
              const targetIdx = selectedTargetPrices?.findIndex(
                p => Math.abs(p - candidate.price) < 1e-4,
              ) ?? -1;
              const rr = showRR ? rewardRisk(candidate.price, entry!, selectedStop!) : null;
              return (
                <tr key={`${candidate.source}-${candidate.price}-${index}`}>
                  <td>
                    <strong>{sourceLabel(candidate.source)}</strong>
                    {isSelectedStop && <span className="risk-updated"> ✓ selected</span>}
                    {targetIdx >= 0 && <span className="positive"> T{targetIdx + 1}</span>}
                  </td>
                  <td>{money(candidate.price, 4)}</td>
                  <td>{pct(candidate.distance_pct)}</td>
                  {showRR && (
                    <td className={rr != null && rr < 1 ? 'negative' : undefined}>
                      {rr != null ? `${rr.toFixed(2)}:1` : '—'}
                    </td>
                  )}
                  <td><small>{candidate.rationale}</small></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SampleQualityBanner({ draft }: { draft: TradePlanDraft }) {
  const empirical = draft.sources?.empirical;
  if (!empirical?.available) return null;

  if (empirical.sufficient) {
    return (
      <div className="card">
        <h3>Empirical sample</h3>
        <p>
          <strong>{empirical.sample_size.toLocaleString()}</strong> comparable signals
          {' · '}confidence <strong>{empirical.confidence}</strong>
          {empirical.win_rate != null && (
            <> · win rate <strong>{(empirical.win_rate * 100).toFixed(1)}%</strong></>
          )}
        </p>
        <p>
          <small>
            Stop uses the p75 adverse excursion
            {empirical.adverse_excursion_pct
              ? ` (${empirical.adverse_excursion_pct.p75.toFixed(2)}%)`
              : ''}
            ; target uses the median favourable excursion
            {empirical.favorable_excursion_pct
              ? ` (${empirical.favorable_excursion_pct.p50.toFixed(2)}%)`
              : ''}
            . Both measured over a 20-bar horizon.
          </small>
        </p>
        {empirical.notes?.map((note: string) => (
          <p key={note} className="risk-data-warning"><small>{note}</small></p>
        ))}
      </div>
    );
  }

  const baseline = (empirical as any).baseline;
  return (
    <div className="card risk-notice">
      <h3>Empirical sample — insufficient</h3>
      <p>
        Only <strong>{empirical.sample_size}</strong> comparable signals (need{' '}
        {empirical.min_sample}); no empirical stop is offered. The plan falls back to
        structural and volatility levels.
      </p>
      {baseline ? (
        <p>
          <small>
            Wider baseline: <strong>{baseline.label}</strong> —{' '}
            {baseline.sample_size.toLocaleString()} signals, confidence{' '}
            <strong>{baseline.confidence}</strong>
            {baseline.adverse_excursion_pct && (
              <> · p75 adverse {baseline.adverse_excursion_pct.p75.toFixed(2)}%</>
            )}
            {baseline.favorable_excursion_pct && (
              <> · median favourable {baseline.favorable_excursion_pct.p50.toFixed(2)}%</>
            )}
            . For context only — this is not conditioned on {draft.symbol}.
          </small>
        </p>
      ) : (
        <p><small>No wider baseline available for this timeframe and direction.</small></p>
      )}
    </div>
  );
}

export function TradePlanningPage({ navigation }: { navigation?: NavigationState }) {
  const [store, setStore] = useState<PlanningStore>(readStore);
  const [symbol, setSymbol] = useState(navigation?.symbol?.toUpperCase() || 'SPY');
  const [timeframe, setTimeframe] = useState(navigation?.timeframe || DEFAULT_TIMEFRAME);
  const [direction, setDirection] = useState<TradeDirection>('long');
  const [includeOptions, setIncludeOptions] = useState(true);

  const [draft, setDraft] = useState<TradePlanDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thesis, setThesis] = useState('');
  const requestVersion = useRef(0);

  useEffect(() => {
    if (navigation?.symbol) setSymbol(navigation.symbol.toUpperCase());
    if (navigation?.timeframe) setTimeframe(navigation.timeframe);
  }, [navigation?.symbol, navigation?.timeframe]);

  // A draft is tied to every setup input. Clear it immediately when any input
  // changes so the page never presents a plan built for a different setup.
  // The version also prevents a slower request from repopulating an obsolete
  // draft after the user edits the form while it is loading.
  useEffect(() => {
    requestVersion.current += 1;
    setDraft(null);
    setError(null);
    setLoading(false);
  }, [symbol, timeframe, direction, includeOptions, store.accountValue, store.riskPercent]);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
    } catch {
      // Storage can be unavailable (private windows, blocked site data); the
      // page stays usable, plans just will not survive a reload.
    }
  }, [store]);

  const buildDraft = useCallback(async () => {
    const resolvedSymbol = await resolveWatchlistSymbol(symbol);
    if (!resolvedSymbol) {
      setError('Choose a symbol from a Watchlist.');
      return;
    }
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    try {
      const result = await api.getTradePlanDraft({
        symbol: resolvedSymbol,
        timeframe,
        direction,
        accountValue: store.accountValue,
        riskPercent: store.riskPercent,
        includeOptions,
      });
      if (version === requestVersion.current) setDraft(result);
    } catch (err: any) {
      if (version === requestVersion.current) {
        setError(err?.message || 'Failed to build trade plan');
        setDraft(null);
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [symbol, timeframe, direction, store.accountValue, store.riskPercent, includeOptions]);

  const plan = draft?.plan ?? null;
  const selected = draft?.selected ?? null;
  const entry = selected?.entry_zone_low ?? draft?.current_price ?? null;

  const riskDollars = plan?.position_size?.risk_dollars ?? null;
  const riskOfAccount = useMemo(() => {
    if (riskDollars == null || !store.accountValue) return null;
    return (riskDollars / store.accountValue) * 100;
  }, [riskDollars, store.accountValue]);

  // Sync saved plans from backend on mount — merge with any localStorage plans
  // that haven't reached the server yet (e.g. created while offline).
  useEffect(() => {
    api.listSavedPlans().then(remote => {
      setStore(prev => {
        const remoteIds = new Set(remote.map((p: any) => p.id));
        const localOnly = prev.plans.filter(p => !remoteIds.has(p.id));
        const merged = [
          ...remote.map((p: any) => ({
            id: p.id,
            symbol: p.symbol,
            side: p.side as TradeDirection,
            timeframe: p.timeframe,
            entryPrice: p.entryPrice,
            stopPrice: p.stopPrice,
            targetPrice: p.targetPrice,
            quantity: p.quantity,
            stopSource: p.stopSource,
            rewardRisk: p.rewardRisk ?? null,
            thesis: p.thesis ?? '',
            createdAt: p.createdAt ?? new Date().toISOString(),
          })),
          ...localOnly,
        ];
        return { ...prev, plans: merged };
      });
    }).catch(() => { /* backend unavailable — localStorage is the source of truth */ });
  }, []);

  const savePlan = () => {
    if (!draft) return;
    const firstTarget = plan?.targets[0];
    // Use auto-selected levels when available; fall back to first candidate when
    // the auto-selector refused (e.g. no target clears the minimum R:R gate).
    const saveEntry = selected?.entry_zone_low ?? draft.current_price ?? 0;
    const saveStop = selected?.stop_price ?? draft.candidate_stops[0]?.price ?? saveEntry;
    const saveTarget =
      firstTarget?.price ??
      selected?.targets[0] ??
      draft.candidate_targets[0]?.price ??
      saveEntry;
    const saveStopSource = selected?.stop_source ?? draft.candidate_stops[0]?.source ?? 'manual';
    const saved: SavedPlan = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      symbol: draft.symbol,
      side: draft.direction,
      timeframe: draft.timeframe,
      entryPrice: saveEntry,
      stopPrice: saveStop,
      targetPrice: saveTarget,
      quantity: Math.floor(plan?.position_size?.shares ?? 0),
      stopSource: saveStopSource,
      rewardRisk: firstTarget?.risk_reward ?? null,
      thesis: thesis.trim(),
      createdAt: new Date().toISOString(),
    };
    setStore(previous => ({ ...previous, plans: [saved, ...previous.plans] }));
    setThesis('');
    // Sync to backend (fire-and-forget — localStorage is already updated above).
    api.upsertSavedPlan({
      client_id: saved.id,
      symbol: saved.symbol,
      side: saved.side,
      timeframe: saved.timeframe,
      entry_price: saved.entryPrice,
      stop_price: saved.stopPrice,
      target_price: saved.targetPrice,
      quantity: saved.quantity,
      stop_source: saved.stopSource,
      reward_risk: saved.rewardRisk,
      thesis: saved.thesis,
    }).catch(() => { /* best-effort */ });
  };

  const removePlan = (id: string) => {
    setStore(previous => ({ ...previous, plans: previous.plans.filter(p => p.id !== id) }));
    api.deleteSavedPlan(id).catch(() => { /* best-effort */ });
  };

  return (
    <div className="trade-planning-page">
      <div className="dashboard-header">
        <div>
          <h1>Trade Planning</h1>
          <p className="subtitle">
            Stop and target candidates from four independent sources, sized against your account.
          </p>
        </div>
      </div>

      <div className="card">
        <h3>Setup</h3>
        <div className="risk-form-grid">
          <label>
            Symbol
            <SymbolAutocompleteInput
              value={symbol}
              onChange={setSymbol}
              onKeyDown={e => { if (e.key === 'Enter') buildDraft(); }}
              placeholder="SPY"
            />
          </label>
          <label>
            Timeframe
            <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
              {TIMEFRAMES.map(tf => (
                <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>
              ))}
            </select>
          </label>
          <label>
            Direction
            <select
              value={direction}
              onChange={e => setDirection(e.target.value as TradeDirection)}
            >
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
          </label>
          <label>
            Account value
            <input
              type="number"
              min="0"
              step="1000"
              value={store.accountValue ?? ''}
              onChange={e =>
                setStore(previous => ({
                  ...previous,
                  accountValue: e.target.value === '' ? null : Number(e.target.value),
                }))
              }
              placeholder="100000"
            />
          </label>
          <label>
            Risk per trade (%)
            <input
              type="number"
              min="0.1"
              max="100"
              step="0.1"
              value={store.riskPercent ?? ''}
              onChange={e =>
                setStore(previous => ({
                  ...previous,
                  riskPercent: e.target.value === '' ? null : Number(e.target.value),
                }))
              }
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={includeOptions}
              onChange={e => setIncludeOptions(e.target.checked)}
            />
            Include options expected move
          </label>
        </div>
        <button className="btn" onClick={buildDraft} disabled={loading}>
          {loading ? 'Building…' : 'Build plan'}
        </button>
        {!store.accountValue && (
          <p className="empty-state">
            <small>Add an account value to size the position.</small>
          </p>
        )}
      </div>

      {error && (
        <div className="card card-error">
          <p>⚠ {error}</p>
          <button className="btn btn-small" onClick={buildDraft}>Retry</button>
        </div>
      )}

      {draft && (
        <>
          {draft.warnings.length > 0 && (
            <div className="card risk-notice">
              <h3>Warnings</h3>
              <ul>
                {draft.warnings.map(warning => <li key={warning}>{warning}</li>)}
              </ul>
            </div>
          )}

          <div className="card">
            <h3>
              {draft.symbol} {draft.timeframe} {draft.direction}
            </h3>
            <p>
              Latest bar close <strong>{money(draft.current_price, 4)}</strong>
              {draft.best_reward_risk != null && (
                <>
                  {' · '}best available R:R{' '}
                  <strong className={draft.best_reward_risk < (draft.min_reward_risk ?? 1) ? 'negative' : 'positive'}>
                    {draft.best_reward_risk.toFixed(2)}:1
                  </strong>
                </>
              )}
            </p>
            <p>
              <small>
                As of {formatAsOf(draft.latest_bar_timestamp)}
                {draft.latest_bar_data_status && ` · ${draft.latest_bar_data_status}`}
                {draft.latest_bar_source && ` · ${draft.latest_bar_source}`}
              </small>
            </p>
            <p>
              <small>
                Sources:{' '}
                {Object.entries(draft.sources).map(([name, payload]: [string, any], i) => (
                  <span key={name}>
                    {i > 0 && ' · '}
                    {sourceLabel(name)}{' '}
                    {payload?.available ? '✓' : `✕ (${payload?.error || 'unavailable'})`}
                  </span>
                ))}
              </small>
            </p>
          </div>

          <SampleQualityBanner draft={draft} />

          <CandidateTable
            title="Candidate stops"
            candidates={draft.candidate_stops}
            entry={entry}
            selectedStop={selected?.stop_price ?? null}
            emptyNote="No stop candidate on the protective side of entry."
          />
          <CandidateTable
            title="Candidate targets"
            candidates={draft.candidate_targets}
            entry={entry}
            selectedStop={selected?.stop_price ?? null}
            selectedTargetPrices={selected?.targets}
            emptyNote="No target candidate beyond entry."
          />

          <div className="card">
            <h3>Plan</h3>
            {plan && selected ? (
              <>
                <div className="session-stats">
                  <span><small>Entry</small><strong>{money(selected.entry_zone_low, 4)}</strong></span>
                  <span>
                    <small>Stop ({sourceLabel(selected.stop_source)})</small>
                    <strong>{money(selected.stop_price, 4)}</strong>
                  </span>
                  <span>
                    <small>Shares</small>
                    <strong>
                      {plan.position_size?.shares != null
                        ? Math.floor(plan.position_size.shares).toLocaleString()
                        : '—'}
                    </strong>
                  </span>
                  <span>
                    <small>Risk</small>
                    <strong>
                      {money(riskDollars)}
                      {riskOfAccount != null && ` (${riskOfAccount.toFixed(2)}%)`}
                    </strong>
                  </span>
                  <span>
                    <small>Position value</small>
                    <strong>{money(plan.position_size?.position_value ?? null, 0)}</strong>
                  </span>
                </div>

                <div className="risk-table-wrap">
                  <table className="risk-table">
                    <thead>
                      <tr>
                        <th scope="col">Target</th>
                        <th scope="col">Price</th>
                        <th scope="col">Reward</th>
                        <th scope="col">R:R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plan.targets.map((target, index) => (
                        <tr key={target.price}>
                          <td>T{index + 1} <small>({sourceLabel(selected.target_sources[index] || '')})</small></td>
                          <td>{money(target.price, 4)}</td>
                          <td>{money(target.reward, 4)}</td>
                          <td><strong>{target.risk_reward.toFixed(2)}:1</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {plan.invalidation && <p><small>{plan.invalidation}</small></p>}
                {!plan.position_size && (
                  <p className="risk-data-warning">
                    <small>{plan.position_size_reason || 'Add account value and risk percentage to size this plan.'}</small>
                  </p>
                )}
                {plan.assumptions.length > 0 && (
                  <details>
                    <summary>Assumptions and formulas</summary>
                    <ul>
                      {plan.assumptions.map(item => <li key={item}><small>{item}</small></li>)}
                      {plan.formulas.map(item => <li key={item}><small><code>{item}</code></small></li>)}
                    </ul>
                  </details>
                )}
              </>
            ) : (
              <p className="empty-state">
                No auto-selected plan — see warnings above. You can still save the setup levels below.
              </p>
            )}

            <label>
              Thesis
              <textarea
                value={thesis}
                onChange={e => setThesis(e.target.value)}
                rows={2}
                placeholder="Why this trade?"
              />
            </label>
            <button className="btn" onClick={savePlan}>
              Save plan
            </button>
          </div>
        </>
      )}

      <div className="card">
        <h3>Saved plans <span className="risk-updated">{store.plans.length}</span></h3>
        {store.plans.length === 0 ? (
          <p className="empty-state">No saved plans yet.</p>
        ) : (
          <div className="risk-table-wrap">
            <table className="risk-table">
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Side</th>
                  <th scope="col">TF</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Stop</th>
                  <th scope="col">Target</th>
                  <th scope="col">Qty</th>
                  <th scope="col">R:R</th>
                  <th scope="col">Thesis</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {store.plans.map(saved => (
                  <tr key={saved.id}>
                    <td><strong>{saved.symbol}</strong></td>
                    <td>{saved.side}</td>
                    <td>{saved.timeframe}</td>
                    <td>{money(saved.entryPrice, 4)}</td>
                    <td>{money(saved.stopPrice, 4)}</td>
                    <td>{money(saved.targetPrice, 4)}</td>
                    <td>{saved.quantity.toLocaleString()}</td>
                    <td>{saved.rewardRisk != null ? `${saved.rewardRisk.toFixed(2)}:1` : '—'}</td>
                    <td><small>{saved.thesis || '—'}</small></td>
                    <td>
                      <button
                        className="btn btn-small"
                        onClick={() => removePlan(saved.id)}
                        aria-label={`Remove ${saved.symbol} plan`}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
