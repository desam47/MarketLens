import React from 'react';

// Highlights "important data" in AI-generated text, text-pattern only
// — no AI schema change. One combined regex (named capture groups, one
// per category) so overlapping candidate spans never fight each other;
// each match is classified by whichever group matched. Shared by
// ChatPanel (assistant replies) and AIAnalysisPanel (summary, trade
// plan thesis/invalidation, supporting/risk factors, key levels).
//
//   - signed numbers (+3.2%, -1.8, +$220): green/red, matching the
//     price tables' .price-up / .price-down convention. The model's
//     prose isn't reliably signed, but it echoes the pre-formatted
//     +/-X figures already in the quant context blocks often enough
//     (see formatChange() in SymbolPage.tsx) to be worth coloring.
//   - ticker mentions: colored by grounding status (focus/partial/
//     unavailable — the same set chat's ✓/◐/✗ provenance pills use;
//     AIAnalysisPanel just passes its one grounded symbol as `focus`).
//   - sentiment/direction words (bullish/breakout/... vs.
//     bearish/breakdown/...): green/red, matched with explicit
//     lower/Upper-first alternates instead of the /i flag — a global
//     case-insensitive match would also light up an unrelated ticker
//     like "IT" or "ALL" wherever it happens to appear lowercase.
//   - metric names (RSI, MACD, support, resistance, volume, ...): a
//     neutral accent, not green/red, since the term itself isn't
//     directionally bullish or bearish.
const BULLISH_WORDS =
  '[Bb]ullish|[Bb]reakout|[Uu]ptrend|[Oo]utperform|[Aa]ccumulation|[Oo]versold|' +
  '[Ss]trength(?:ening)?|[Ss]trong(?:er)?|[Bb]uy|[Uu]pside|[Rr]ally(?:ing)?|[Bb]ounce';
const BEARISH_WORDS =
  '[Bb]earish|[Bb]reakdown|[Dd]owntrend|[Uu]nderperform|[Dd]istribution|[Oo]verbought|' +
  '[Ww]eak(?:ness|er)?|[Ss]ell(?:-?off)?|[Dd]ownside|[Pp]ullback|[Cc]orrection|[Rr]ollover';
const METRIC_WORDS = 'RSI|MACD|ADX|EMA|SMA|VWAP|ATR|[Ss]upport|[Rr]esistance|[Vv]olume';

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function tickerAlternation(tickers: string[]): string {
  return Array.from(new Set(tickers.filter(Boolean)))
    .map(escapeRegex)
    .sort((a, b) => b.length - a.length)
    .join('|');
}

function buildHighlightRegex(focus: string[], partial: string[], unavailable: string[]): RegExp {
  const parts = ['(?<num>[+-]\\d[\\d,]*\\.?\\d*%?)'];
  const ok = tickerAlternation(focus);
  const part = tickerAlternation(partial);
  const none = tickerAlternation(unavailable);
  if (ok) parts.push(`(?<tickOk>\\b(?:${ok})\\b)`);
  if (part) parts.push(`(?<tickPartial>\\b(?:${part})\\b)`);
  if (none) parts.push(`(?<tickNone>\\b(?:${none})\\b)`);
  parts.push(`(?<bull>\\b(?:${BULLISH_WORDS})\\b)`);
  parts.push(`(?<bear>\\b(?:${BEARISH_WORDS})\\b)`);
  parts.push(`(?<metric>\\b(?:${METRIC_WORDS})\\b)`);
  // A bare (unsigned) number — "RSI is oversold around 26.6" — has no
  // sign of its own to color by, but it's the value a nearby metric or
  // sentiment word was just talking about. Colored by whichever of
  // those was last seen in the current sentence (see the scan below);
  // left plain when nothing in the sentence so far said what it means
  // ("20 shares", "the 50-day average").
  parts.push('(?<bare>\\b\\d[\\d,]*\\.?\\d*%?\\b)');
  // Sentence boundary — clears that running context so a number in an
  // unrelated later sentence doesn't inherit stale sentiment.
  parts.push('(?<term>[.!?])');
  return new RegExp(parts.join('|'), 'g');
}

type NumberContext = 'pos' | 'neg' | 'metric' | null;

export function highlightMessage(
  text: string, focus: string[] = [], partial: string[] = [], unavailable: string[] = [],
): React.ReactNode[] {
  const re = buildHighlightRegex(focus, partial, unavailable);
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let context: NumberContext = null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const token = match[0];
    const groups = match.groups ?? {};
    let cls: string | null = null;
    if (groups.num) {
      cls = token.startsWith('-') ? 'chat-num-neg' : 'chat-num-pos';
    } else if (groups.tickOk) {
      cls = 'chat-ticker-ok';
    } else if (groups.tickPartial) {
      cls = 'chat-ticker-partial';
    } else if (groups.tickNone) {
      cls = 'chat-ticker-none';
    } else if (groups.bull) {
      cls = 'chat-num-pos';
      context = 'pos';
    } else if (groups.bear) {
      cls = 'chat-num-neg';
      context = 'neg';
    } else if (groups.metric) {
      cls = 'chat-metric';
      context = 'metric';
    } else if (groups.bare) {
      if (context === 'pos') cls = 'chat-num-pos';
      else if (context === 'neg') cls = 'chat-num-neg';
      else if (context === 'metric') cls = 'chat-metric';
    } else if (groups.term) {
      context = null;
    }
    parts.push(cls ? <span key={match.index} className={cls}>{token}</span> : token);
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}
