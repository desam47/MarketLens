import React, { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import api, { Bar } from '../services/api';
import { earningsDaysAway } from '../components/EarningsBadge';
import type { NavigationState } from '../utils/appNavigation';

const STORAGE_KEY = 'marketlens.risk.positions';
const EARNINGS_WINDOW_KEY = 'marketlens.risk.earnings-warning-days';
const TRADING_DAYS = 252;
const EARNINGS_WARNING_WINDOWS = [3, 7, 14, 30];

export interface ManualPosition {
  id: string;
  symbol: string;
  side: 'long' | 'short';
  quantity: number;
  entryPrice: number;
  stopPrice: number | null;
  sector?: string | null;
}

interface PositionSnapshot extends ManualPosition {
  currentPrice: number | null;
  marketValue: number;
  pnl: number | null;
  stopRisk: number | null;
  weight: number;
  volatility: number | null;
  earningsDays: number | null;
  bars: Bar[];
  returns: Map<string, number>;
  dataError?: string;
}

interface PortfolioStats {
  grossValue: number;
  netValue: number;
  totalPnl: number | null;
  stopRisk: number | null;
  maxDrawdown: number | null;
  maxDrawdownDollar: number | null;
  volatility: number | null;
  largestConcentration: number;
  largestSymbol: string | null;
  correlations: Array<{ first: string; second: string; value: number }>;
  sectorExposure: Array<{ sector: string; value: number; weight: number }>;
}

function readPositions(): ManualPosition[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((position): position is ManualPosition => (
      position && typeof position.id === 'string' && typeof position.symbol === 'string'
      && (position.side === 'long' || position.side === 'short')
      && Number.isFinite(position.quantity) && position.quantity > 0
      && Number.isFinite(position.entryPrice) && position.entryPrice > 0
    ));
  } catch {
    return [];
  }
}

function readEarningsWarningDays(): number {
  const value = Number(window.localStorage.getItem(EARNINGS_WINDOW_KEY));
  return EARNINGS_WARNING_WINDOWS.includes(value) ? value : 7;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function standardDeviation(values: number[]): number | null {
  if (values.length < 2) return null;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / (values.length - 1);
  return Math.sqrt(Math.max(0, variance));
}

function correlation(left: number[], right: number[]): number | null {
  const count = Math.min(left.length, right.length);
  if (count < 3) return null;
  const a = left.slice(0, count);
  const b = right.slice(0, count);
  const meanA = a.reduce((sum, value) => sum + value, 0) / count;
  const meanB = b.reduce((sum, value) => sum + value, 0) / count;
  let covariance = 0;
  let varianceA = 0;
  let varianceB = 0;
  for (let index = 0; index < count; index += 1) {
    const da = a[index] - meanA;
    const db = b[index] - meanB;
    covariance += da * db;
    varianceA += da * da;
    varianceB += db * db;
  }
  if (varianceA === 0 || varianceB === 0) return null;
  return covariance / Math.sqrt(varianceA * varianceB);
}

function returnsForBars(bars: Bar[]): Map<string, number> {
  const sorted = [...bars]
    .filter(bar => finite(bar.close) && finite(Date.parse(bar.timestamp)))
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const result = new Map<string, number>();
  for (let index = 1; index < sorted.length; index += 1) {
    const previous = sorted[index - 1].close;
    if (previous !== 0) result.set(sorted[index].timestamp, (sorted[index].close / previous) - 1);
  }
  return result;
}

function fmtMoney(value: number | null, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function getQuotePrice(value: any): number | null {
  return finite(value?.price) ? value.price : null;
}

function calculatePortfolioStats(snapshots: PositionSnapshot[]): PortfolioStats {
  const grossValue = snapshots.reduce((sum, item) => sum + item.marketValue, 0);
  const netValue = snapshots.reduce((sum, item) => sum + (item.marketValue * (item.side === 'short' ? -1 : 1)), 0);
  const usablePnl = snapshots.map(item => item.pnl).filter(finite);
  const usableStopRisk = snapshots.map(item => item.stopRisk).filter(finite);
  const totalPnl = usablePnl.length === snapshots.length && snapshots.length > 0 ? usablePnl.reduce((sum, value) => sum + value, 0) : null;
  const stopRisk = usableStopRisk.length === snapshots.length && snapshots.length > 0 ? usableStopRisk.reduce((sum, value) => sum + value, 0) : null;
  const largest = snapshots.reduce<PositionSnapshot | null>((best, item) => !best || item.weight > best.weight ? item : best, null);

  const sectorMap = new Map<string, number>();
  snapshots.forEach(item => {
    const sector = item.sector?.trim() || 'Unknown';
    sectorMap.set(sector, (sectorMap.get(sector) || 0) + item.marketValue);
  });
  const sectorExposure = Array.from(sectorMap.entries())
    .map(([sector, value]) => ({ sector, value, weight: grossValue ? (value / grossValue) * 100 : 0 }))
    .sort((a, b) => b.value - a.value);

  const correlations: PortfolioStats['correlations'] = [];
  for (let left = 0; left < snapshots.length; left += 1) {
    for (let right = left + 1; right < snapshots.length; right += 1) {
      const first = snapshots[left];
      const second = snapshots[right];
      const shared = Array.from(first.returns.keys()).filter(key => second.returns.has(key)).sort();
      const value = correlation(shared.map(key => first.returns.get(key) as number), shared.map(key => second.returns.get(key) as number));
      if (value != null) correlations.push({ first: first.symbol, second: second.symbol, value });
    }
  }
  correlations.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  const allDates = new Set<string>();
  snapshots.forEach(item => item.returns.forEach((_value, key) => allDates.add(key)));
  const dates = Array.from(allDates).sort();
  const portfolioReturns: number[] = [];
  dates.forEach(date => {
    let weighted = 0;
    let availableWeight = 0;
    snapshots.forEach(item => {
      const value = item.returns.get(date);
      if (value != null && grossValue > 0) {
        const direction = item.side === 'short' ? -1 : 1;
        weighted += value * direction * (item.marketValue / grossValue);
        availableWeight += item.marketValue / grossValue;
      }
    });
    if (availableWeight > 0) portfolioReturns.push(weighted / availableWeight);
  });
  const dailyVolatility = standardDeviation(portfolioReturns);
  const volatility = dailyVolatility == null ? null : dailyVolatility * Math.sqrt(TRADING_DAYS) * 100;
  let equity = 1;
  let peak = 1;
  let maxDrawdown = 0;
  portfolioReturns.forEach(value => {
    equity *= 1 + value;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak > 0 ? (peak - equity) / peak : 0);
  });

  return {
    grossValue,
    netValue,
    totalPnl,
    stopRisk,
    maxDrawdown: portfolioReturns.length >= 2 ? maxDrawdown * 100 : null,
    maxDrawdownDollar: portfolioReturns.length >= 2 ? maxDrawdown * grossValue : null,
    volatility,
    largestConcentration: largest?.weight || 0,
    largestSymbol: largest?.symbol || null,
    correlations,
    sectorExposure,
  };
}

export function RiskDashboardPage({ navigation }: { navigation?: NavigationState }) {
  const [positions, setPositions] = useState<ManualPosition[]>(readPositions);
  const [snapshots, setSnapshots] = useState<PositionSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [symbol, setSymbol] = useState(navigation?.symbol || '');
  const [side, setSide] = useState<'long' | 'short'>('long');
  const [quantity, setQuantity] = useState('');
  const [entryPrice, setEntryPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [sector, setSector] = useState('');
  const [earningsWarningDays, setEarningsWarningDays] = useState(readEarningsWarningDays);
  const [focusedPositionIds, setFocusedPositionIds] = useState<string[]>(navigation?.selectedRecords || []);

  useEffect(() => {
    if (navigation?.symbol) setSymbol(navigation.symbol);
    setFocusedPositionIds(navigation?.selectedRecords || []);
  }, [navigation]);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(positions));
  }, [positions]);

  useEffect(() => {
    window.localStorage.setItem(EARNINGS_WINDOW_KEY, String(earningsWarningDays));
  }, [earningsWarningDays]);

  const loadRiskData = useCallback(async () => {
    if (!positions.length) {
      setSnapshots([]);
      return;
    }
    setLoading(true);
    setError(null);
    const loaded = await Promise.all(positions.map(async (position): Promise<PositionSnapshot> => {
      const [quoteResult, barsResult, fundamentalsResult, calendarResult] = await Promise.allSettled([
        api.getQuote(position.symbol),
        api.getAnalysisBars(position.symbol, '1d', 90),
        position.sector ? Promise.resolve(null) : api.getFundamentals(position.symbol),
        api.getSymbolCalendar(position.symbol),
      ]);
      const quote = quoteResult.status === 'fulfilled' ? getQuotePrice(quoteResult.value) : null;
      const bars = barsResult.status === 'fulfilled' && Array.isArray(barsResult.value.bars) ? barsResult.value.bars : [];
      const latestBar = [...bars].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0];
      const currentPrice = quote ?? (finite(latestBar?.close) ? latestBar.close : null);
      const marketValue = (currentPrice ?? position.entryPrice) * position.quantity;
      const direction = position.side === 'short' ? -1 : 1;
      const pnl = currentPrice == null ? null : (currentPrice - position.entryPrice) * position.quantity * direction;
      const stopRisk = position.stopPrice == null ? null : Math.max(0, (position.entryPrice - position.stopPrice) * position.quantity * direction);
      const fundamentals = fundamentalsResult.status === 'fulfilled' ? fundamentalsResult.value : null;
      const resolvedSector = position.sector || fundamentals?.data?.sector || null;
      const returns = returnsForBars(bars);
      const volatilityDaily = standardDeviation(Array.from(returns.values()));
      const earningsDays = calendarResult.status === 'fulfilled'
        ? earningsDaysAway(calendarResult.value.events)
        : null;
      return {
        ...position,
        sector: resolvedSector,
        currentPrice,
        marketValue,
        pnl,
        stopRisk,
        weight: 0,
        volatility: volatilityDaily == null ? null : volatilityDaily * Math.sqrt(TRADING_DAYS) * 100,
        earningsDays,
        bars,
        returns,
        dataError: quoteResult.status === 'rejected' && barsResult.status === 'rejected' ? 'Market data unavailable' : undefined,
      };
    }));
    const gross = loaded.reduce((sum, item) => sum + item.marketValue, 0);
    setSnapshots(loaded.map(item => ({ ...item, weight: gross ? (item.marketValue / gross) * 100 : 0 })));
    setLastUpdated(new Date());
    setLoading(false);
  }, [positions]);

  useEffect(() => { void loadRiskData(); }, [loadRiskData]);

  const stats = useMemo(() => calculatePortfolioStats(snapshots), [snapshots]);
  const upcomingEarnings = useMemo(
    () => snapshots.filter(item => item.earningsDays != null && item.earningsDays >= 0 && item.earningsDays <= earningsWarningDays),
    [snapshots, earningsWarningDays],
  );

  const addPosition = (event: FormEvent) => {
    event.preventDefault();
    const normalizedSymbol = symbol.trim().toUpperCase();
    const parsedQuantity = Number(quantity);
    const parsedEntry = Number(entryPrice);
    const parsedStop = stopPrice.trim() ? Number(stopPrice) : null;
    if (!normalizedSymbol || !Number.isFinite(parsedQuantity) || parsedQuantity <= 0 || !Number.isFinite(parsedEntry) || parsedEntry <= 0) {
      setError('Enter a symbol, a positive quantity, and a valid entry price.');
      return;
    }
    if (parsedStop != null && (!Number.isFinite(parsedStop) || parsedStop <= 0)) {
      setError('Stop price must be blank or a positive number.');
      return;
    }
    const existing = positions.find(position => position.symbol === normalizedSymbol && position.side === side);
    const next: ManualPosition = {
      id: existing?.id || `${normalizedSymbol}-${side}-${Date.now()}`,
      symbol: normalizedSymbol,
      side,
      quantity: existing ? existing.quantity + parsedQuantity : parsedQuantity,
      entryPrice: existing ? ((existing.entryPrice * existing.quantity) + (parsedEntry * parsedQuantity)) / (existing.quantity + parsedQuantity) : parsedEntry,
      stopPrice: parsedStop,
      sector: sector.trim() || existing?.sector || null,
    };
    setPositions(current => [...current.filter(position => position.id !== existing?.id), next]);
    setSymbol(''); setQuantity(''); setEntryPrice(''); setStopPrice(''); setSector(''); setError(null);
  };

  const removePosition = (id: string) => setPositions(current => current.filter(position => position.id !== id));

  return (
    <div className="page risk-dashboard-page">
      <div className="dashboard-header">
        <div>
          <h1>Risk Dashboard</h1>
          <p className="subtitle">Track manually entered positions, downside risk, concentration, correlation, volatility, and drawdown.</p>
        </div>
        <div className="header-actions">
          {lastUpdated && <span className="risk-updated">Updated {lastUpdated.toLocaleTimeString()}</span>}
          <button className="btn" onClick={() => void loadRiskData()} disabled={loading}>{loading ? 'Refreshing…' : '↻ Refresh'}</button>
        </div>
      </div>

      <div className="risk-notice">Manual tracker only — prices and historical metrics use the existing market-data providers. Nothing here places trades or syncs with a broker.</div>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}

      <form className="card risk-position-form" onSubmit={addPosition}>
        <div className="risk-form-heading"><div><h2>Add position</h2><span className="info-text">Add again to the same symbol and side to increase the position and update its average entry.</span></div></div>
        <div className="risk-form-grid">
          <label>Symbol<input value={symbol} onChange={event => setSymbol(event.target.value)} placeholder="AAPL" /></label>
          <label>Side<select value={side} onChange={event => setSide(event.target.value as 'long' | 'short')}><option value="long">Long</option><option value="short">Short</option></select></label>
          <label>Quantity<input type="number" min="0" step="any" value={quantity} onChange={event => setQuantity(event.target.value)} placeholder="100" /></label>
          <label>Entry price<input type="number" min="0" step="any" value={entryPrice} onChange={event => setEntryPrice(event.target.value)} placeholder="185.00" /></label>
          <label>Stop price <span className="label-muted">(optional)</span><input type="number" min="0" step="any" value={stopPrice} onChange={event => setStopPrice(event.target.value)} placeholder="175.00" /></label>
          <label>Sector <span className="label-muted">(optional)</span><input value={sector} onChange={event => setSector(event.target.value)} placeholder="Technology" /></label>
          <button className="btn btn-primary risk-add-button" type="submit">+ Add position</button>
        </div>
      </form>

      {positions.length === 0 ? (
        <div className="card risk-empty-state"><div className="risk-empty-icon">🛡️</div><h2>No positions yet</h2><p>Add a position above to see live exposure and risk metrics.</p></div>
      ) : (
        <>
          <div className="risk-summary-grid">
            <div className="card risk-summary-card"><span>Gross exposure</span><strong>{fmtMoney(stats.grossValue)}</strong><small>Net {fmtMoney(stats.netValue)}</small><small>{stats.largestSymbol ? `Largest: ${stats.largestSymbol} (${stats.largestConcentration.toFixed(1)}%)` : 'No concentration data'}</small></div>
            <div className="card risk-summary-card"><span>Stop-loss risk</span><strong>{fmtMoney(stats.stopRisk)}</strong><small>{stats.grossValue ? `${((stats.stopRisk || 0) / stats.grossValue * 100).toFixed(1)}% of exposure` : 'Add stops to measure'}</small></div>
            <div className="card risk-summary-card"><span>Portfolio volatility</span><strong>{stats.volatility == null ? '—' : `${stats.volatility.toFixed(1)}%`}</strong><small>Annualized from daily returns</small></div>
            <div className="card risk-summary-card"><span>Maximum drawdown</span><strong>{stats.maxDrawdown == null ? '—' : stats.maxDrawdown > 0 ? `-${stats.maxDrawdown.toFixed(1)}%` : '0.0%'}</strong><small>{stats.maxDrawdownDollar == null ? 'Need shared history' : `${fmtMoney(stats.maxDrawdownDollar)} at current size`}</small></div>
          </div>

          <section className="card risk-earnings-card">
            <div className="risk-card-heading">
              <h2>Earnings risk</h2>
              <label className="risk-earnings-window">Warn within <select value={earningsWarningDays} onChange={event => setEarningsWarningDays(Number(event.target.value))}>{EARNINGS_WARNING_WINDOWS.map(days => <option key={days} value={days}>{days} days</option>)}</select></label>
            </div>
            {upcomingEarnings.length === 0 ? <p className="info-text">No tracked positions have a provider-estimated earnings date within {earningsWarningDays} days.</p> : <div className="risk-earnings-list">{upcomingEarnings.map(item => <span className="risk-earnings-warning" key={item.id}><strong>{item.symbol}</strong> reports {item.earningsDays === 0 ? 'today' : `in ${item.earningsDays} day${item.earningsDays === 1 ? '' : 's'}`}</span>)}</div>}
          </section>

          <div className="risk-layout-grid">
            <section className="card risk-positions-card"><div className="risk-card-heading"><h2>Positions</h2><span>{snapshots.length} tracked</span></div>{focusedPositionIds.length > 0 && <p className="info-text" role="status">Focused positions are highlighted from the originating Chat action.</p>}<div className="risk-table-wrap"><table className="risk-table"><thead><tr><th scope="col">Symbol</th><th scope="col">Quantity</th><th scope="col">Price</th><th scope="col">Exposure</th><th scope="col">Weight</th><th scope="col">P&amp;L</th><th scope="col">Stop risk</th><th scope="col">Volatility</th><th scope="col">Earnings</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead><tbody>{snapshots.map(item => <tr key={item.id} className={focusedPositionIds.includes(item.id) ? 'risk-position-focused' : undefined} aria-label={focusedPositionIds.includes(item.id) ? `${item.symbol} focused position` : undefined}><td><strong>{item.symbol}</strong><small className={`risk-side-${item.side}`}>{item.side}</small>{item.dataError && <small className="risk-data-warning">Data unavailable</small>}</td><td>{item.quantity.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td><td>{fmtMoney(item.currentPrice, 2)}</td><td>{fmtMoney(item.marketValue)}</td><td>{item.weight.toFixed(1)}%</td><td className={item.pnl != null && item.pnl >= 0 ? 'risk-positive' : 'risk-negative'}>{fmtMoney(item.pnl)}</td><td>{fmtMoney(item.stopRisk)}</td><td>{item.volatility == null ? '—' : `${item.volatility.toFixed(1)}%`}</td><td className={item.earningsDays != null && item.earningsDays >= 0 && item.earningsDays <= earningsWarningDays ? 'risk-earnings-cell-warning' : ''}>{item.earningsDays == null ? '—' : item.earningsDays < 0 ? 'Reported' : item.earningsDays === 0 ? 'Today' : `In ${item.earningsDays}d`}</td><td><button className="risk-remove" onClick={() => removePosition(item.id)} aria-label={`Remove ${item.symbol}`}>×</button></td></tr>)}</tbody></table></div></section>
            <section className="card risk-sector-card"><div className="risk-card-heading"><h2>Sector exposure</h2><span>by market value</span></div>{stats.sectorExposure.map(item => <div className="risk-bar-row" key={item.sector}><div><span>{item.sector}</span><strong>{item.weight.toFixed(1)}%</strong></div><div className="risk-bar"><span style={{ width: `${Math.min(100, item.weight)}%` }} /></div><small>{fmtMoney(item.value)}</small></div>)}</section>
          </div>

          <section className="card risk-correlation-card"><div className="risk-card-heading"><h2>Correlation</h2><span>90 daily bars, overlapping dates</span></div>{stats.correlations.length === 0 ? <p className="info-text">Add at least two positions with shared historical data to compare correlation.</p> : <div className="risk-correlation-list">{stats.correlations.slice(0, 8).map(item => <div className="risk-correlation-row" key={`${item.first}-${item.second}`}><span>{item.first} ↔ {item.second}</span><strong className={Math.abs(item.value) >= 0.7 ? 'risk-negative' : ''}>{item.value.toFixed(2)}</strong><small>{Math.abs(item.value) >= 0.7 ? 'High co-movement' : Math.abs(item.value) <= 0.2 ? 'Low co-movement' : 'Moderate co-movement'}</small></div>)}</div>}</section>
        </>
      )}
    </div>
  );
}

export default RiskDashboardPage;
