import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api, { TradePlanDraft } from '../services/api';
import { TradePlanningPage } from './TradePlanningPage';

const STORAGE_KEY = 'marketlens.trade.plans';

function draftWithPlan(): TradePlanDraft {
  return {
    symbol: 'SPY',
    timeframe: '5m',
    direction: 'long',
    current_price: 771.61,
    latest_bar_timestamp: '2026-09-25T15:30:00Z',
    latest_bar_data_status: 'historical',
    latest_bar_source: 'alpaca',
    bars_used: 500,
    sources: {
      structural: { available: true },
      volatility: { available: true },
      empirical: {
        available: true,
        sample_size: 663,
        min_sample: 100,
        sufficient: true,
        confidence: 'high',
        win_rate: 0.5098,
        adverse_excursion_pct: { p25: 0.03, p50: 0.086, p75: 0.19, p90: 0.36 },
        favorable_excursion_pct: { p25: 0.037, p50: 0.079, p75: 0.152, p90: 0.272 },
        notes: [],
      },
      options_implied: { available: false, error: 'Options provider disabled.' },
    },
    candidate_stops: [
      { source: 'structural', price: 771.09, distance_pct: 0.0527, rationale: 'Nearest swing low.' },
      { source: 'empirical', price: 770.13, distance_pct: 0.19, rationale: 'p75 adverse excursion.' },
    ],
    candidate_targets: [
      { source: 'structural', price: 773.21, distance_pct: 0.207, rationale: 'swing high.' },
    ],
    best_reward_risk: 3.24,
    min_reward_risk: 1.0,
    selected: {
      entry_zone_low: 771.61,
      entry_zone_high: 771.61,
      stop_price: 770.13,
      stop_source: 'empirical',
      targets: [773.21],
      target_sources: ['structural'],
    },
    plan: {
      available: true,
      symbol: 'SPY',
      direction: 'long',
      entry_zone: { low: 771.61, high: 771.61 },
      entry_reference: 771.61,
      stop_price: 770.13,
      targets: [{ price: 773.21, risk: 1.48, reward: 1.6, risk_reward: 1.08 }],
      position_size: {
        per_share_risk: 1.48,
        risk_dollars: 1000,
        shares: 675.6,
        position_value: 521400,
        portfolio_risk_percent: 1.0,
      },
      position_size_reason: null,
      invalidation: 'Plan invalidated if SPY closes below the stop (770.13).',
      timeframe: '5m',
      formulas: ['reward / risk'],
      assumptions: ['Position size is fractional; round down.'],
    },
    warnings: ['Position value $521,400 exceeds account value $100,000 (5.2x)'],
  };
}

function draftWithoutPlan(): TradePlanDraft {
  const base = draftWithPlan();
  return {
    ...base,
    best_reward_risk: 0.77,
    selected: null,
    plan: null,
    candidate_targets: [],
    sources: {
      ...base.sources,
      empirical: {
        available: true,
        sample_size: 41,
        min_sample: 100,
        sufficient: false,
        confidence: 'insufficient',
        win_rate: null,
        adverse_excursion_pct: null,
        favorable_excursion_pct: null,
        notes: ['Only 41 comparable signals (need 100); statistics withheld.'],
      },
    },
    warnings: ['No target clears 1:1 against the 3.51% empirical stop -- the best available is 0.77:1.'],
  };
}

function draftWithoutSizing(): TradePlanDraft {
  const base = draftWithPlan();
  return {
    ...base,
    plan: {
      ...base.plan!,
      position_size: null,
      position_size_reason: 'Provide account_value and risk_percent to size the position; no default was assumed.',
    },
  };
}

describe('TradePlanningPage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    jest.restoreAllMocks();
    jest.spyOn(api, 'getSymbolCatalog').mockResolvedValue(['AAPL', 'SPY']);
    jest.spyOn(api, 'listSavedPlans').mockResolvedValue([]);
    jest.spyOn(api, 'upsertSavedPlan').mockResolvedValue({});
    jest.spyOn(api, 'deleteSavedPlan').mockResolvedValue(undefined);
  });

  it('renders the setup form and no plan before building', () => {
    render(<TradePlanningPage />);
    expect(screen.getByRole('heading', { name: /Trade Planning/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Build plan/i })).toBeInTheDocument();
    expect(screen.getByText(/No saved plans yet/i)).toBeInTheDocument();
  });

  it('prefills symbol and timeframe from navigation (SymbolPage handoff)', () => {
    render(<TradePlanningPage navigation={{ symbol: 'AAPL', timeframe: '1h' }} />);
    expect(screen.getByDisplayValue('AAPL')).toBeInTheDocument();
  });

  it('renders candidates, sample quality and the assembled plan', async () => {
    const spy = jest.spyOn(api, 'getTradePlanDraft').mockResolvedValue(draftWithPlan());
    render(<TradePlanningPage />);

    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));
    await waitFor(() => expect(spy).toHaveBeenCalled());

    // Candidate stops from both sources are shown for comparison.
    expect(await screen.findByText(/Candidate stops/i)).toBeInTheDocument();
    expect(screen.getByText(/Nearest swing low/i)).toBeInTheDocument();
    // Appears both as a candidate rationale and in the sample-quality note.
    expect(screen.getAllByText(/p75 adverse excursion/i).length).toBeGreaterThan(0);

    // Sample quality is surfaced honestly.
    expect(screen.getByText(/663/)).toBeInTheDocument();
    expect(screen.getByText(/comparable signals/i)).toBeInTheDocument();

    // The reward:risk of the selected pairing.
    // Shown in both the candidate R:R column and the plan's target ladder.
    expect(screen.getAllByText(/1\.08:1/).length).toBeGreaterThan(0);

    // An unavailable source is reported, not hidden.
    expect(screen.getByText(/Options provider disabled/i)).toBeInTheDocument();

    expect(screen.getByText(/Latest bar close/i)).toBeInTheDocument();
    expect(screen.getByText(/historical · alpaca/i)).toBeInTheDocument();

    // The leverage warning is visible.
    expect(screen.getByText(/exceeds account value/i)).toBeInTheDocument();
  });

  it('shows the refusal and best-achievable ratio when nothing clears the gate', async () => {
    jest.spyOn(api, 'getTradePlanDraft').mockResolvedValue(draftWithoutPlan());
    render(<TradePlanningPage />);

    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));

    expect(await screen.findByText(/the best available is 0\.77:1/i)).toBeInTheDocument();
    expect(screen.getByText(/No auto-selected plan/i)).toBeInTheDocument();
    // A thin sample must not present a fabricated stop.
    expect(screen.getByRole('heading', { name: /Empirical sample . insufficient/i })).toBeInTheDocument();
    // The count sits in a nested <strong>, so match on normalised text.
    expect(
      screen.getAllByText((_, node) =>
        (node?.textContent || '').replace(/\s+/g, ' ').includes('Only 41 comparable signals'),
      ),
    ).not.toHaveLength(0);
  });

  it('saves a plan to localStorage and can remove it', async () => {
    jest.spyOn(api, 'getTradePlanDraft').mockResolvedValue(draftWithPlan());
    render(<TradePlanningPage />);

    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));
    const save = await screen.findByRole('button', { name: /^Save plan$/i });
    fireEvent.click(save);

    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      expect(stored.plans).toHaveLength(1);
      expect(stored.plans[0].symbol).toBe('SPY');
      expect(stored.plans[0].stopSource).toBe('empirical');
      // Shares are floored: a fractional order is not placeable.
      expect(stored.plans[0].quantity).toBe(675);
    });

    fireEvent.click(screen.getByRole('button', { name: /Remove SPY plan/i }));
    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      expect(stored.plans).toHaveLength(0);
    });
  });

  it('saves an unsized plan with quantity 0 and shows the sizing prompt', async () => {
    jest.spyOn(api, 'getTradePlanDraft').mockResolvedValue(draftWithoutSizing());
    render(<TradePlanningPage />);

    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));
    const save = await screen.findByRole('button', { name: /^Save plan$/i });

    // Save is enabled even without sizing so traders can record levels.
    expect(save).not.toBeDisabled();
    expect(screen.getByText(/Provide account_value and risk_percent/i)).toBeInTheDocument();

    fireEvent.click(save);
    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      expect(stored.plans).toHaveLength(1);
      expect(stored.plans[0].quantity).toBe(0);
    });
  });

  it('clears a built draft when setup inputs change', async () => {
    jest.spyOn(api, 'getTradePlanDraft').mockResolvedValue(draftWithPlan());
    render(<TradePlanningPage />);

    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));
    expect(await screen.findByText(/Candidate stops/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('100000'), { target: { value: '50000' } });

    await waitFor(() => expect(screen.queryByText(/Candidate stops/i)).not.toBeInTheDocument());
  });

  it('persists account value and risk percent', async () => {
    render(<TradePlanningPage />);
    fireEvent.change(screen.getByPlaceholderText('100000'), { target: { value: '250000' } });

    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      expect(stored.accountValue).toBe(250000);
    });
  });

  it('passes account value and risk percent to the API', async () => {
    const spy = jest.spyOn(api, 'getTradePlanDraft').mockResolvedValue(draftWithPlan());
    render(<TradePlanningPage />);

    fireEvent.change(screen.getByPlaceholderText('100000'), { target: { value: '50000' } });
    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ accountValue: 50000, riskPercent: 1 }),
      ),
    );
  });

  it('surfaces an API failure with a retry', async () => {
    jest.spyOn(api, 'getTradePlanDraft').mockRejectedValue(new Error('boom'));
    render(<TradePlanningPage />);

    fireEvent.click(screen.getByRole('button', { name: /Build plan/i }));
    expect(await screen.findByText(/boom/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  it('merges remote plans with localStorage on mount, deduplicating by id', async () => {
    const remotePlan = {
      id: 'remote-1',
      symbol: 'AAPL',
      side: 'long',
      timeframe: '1d',
      entryPrice: 220,
      stopPrice: 215,
      targetPrice: 230,
      quantity: 10,
      stopSource: 'structural',
      rewardRisk: 2.0,
      thesis: 'breakout',
      createdAt: '2026-09-20T10:00:00.000Z',
    };
    // A local-only plan that should survive the merge.
    const localOnlyPlan = {
      id: 'local-only',
      symbol: 'SPY',
      side: 'short',
      timeframe: '5m',
      entryPrice: 500,
      stopPrice: 505,
      targetPrice: 490,
      quantity: 5,
      stopSource: 'volatility',
      rewardRisk: 1.5,
      thesis: '',
      createdAt: '2026-09-21T08:00:00.000Z',
    };
    // A local plan whose id collides with the remote — the remote version wins.
    const localDupe = { ...remotePlan, entryPrice: 999 };

    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ accountValue: null, riskPercent: 1, plans: [localDupe, localOnlyPlan] }),
    );

    jest.spyOn(api, 'listSavedPlans').mockResolvedValue([remotePlan]);

    render(<TradePlanningPage />);

    // After mount the merge runs and the table should contain exactly two rows.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Remove AAPL plan/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Remove SPY plan/i })).toBeInTheDocument();
    });

    // The remote entry price (220) wins over the local dupe (999).
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
    expect(stored.plans).toHaveLength(2);
    const aapl = stored.plans.find((p: any) => p.id === 'remote-1');
    expect(aapl?.entryPrice).toBe(220);
    expect(aapl?.createdAt).toBe('2026-09-20T10:00:00.000Z');
  });
});
