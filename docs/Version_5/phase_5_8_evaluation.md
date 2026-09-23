# Phase 5.8 Evaluation Report

**Harness version:** `5.8.0`
**Verifier version:** `5.8.1`
**Scope:** provider-free verifier and fallback-contract evaluation

## Categories

Each case is scored for correctness, tool choice, provenance, clarification
quality, latency-budget compliance, and action safety. Runtime latency and
provider-request measurements come from the persisted observability event;
provider-free cases use a zero external-latency budget.

## Current result

| Category | Passed | Total |
| --- | ---: | ---: |
| Correctness | 19 | 19 |
| Tool choice | 19 | 19 |
| Provenance | 19 | 19 |
| Clarification | 19 | 19 |
| Latency | 19 | 19 |
| Safety | 19 | 19 |

The 19 cases cover verified quotes and calculations, altered numbers,
calculation mismatches, wrong units, prose/evidence contradictions, sessions,
timeframes, unknown tickers, freshness and stale-live claims, options,
comparisons, scanner results, portfolio risk, follow-up clarification,
adversarial instructions, provider failure, and destructive-action
confirmation without execution.

## Semantic routing hardening follow-up

The post-gate semantic-routing slice adds a canonical `SemanticRoute` layer,
the read-only `get_watchlist_intelligence` Chat tool, server-owned watchlist
scope resolution, evidence-only watchlist formatting, on-demand warming of
missing scanner symbols, and the same route in blocking and streaming Chat.
Provider-free coverage includes exact and
paraphrased watchlist questions, common symbol research questions, aggregated
multi-watchlist scope, and the tool-registry contract. The existing 19-case
scored harness remains unchanged; open-ended requests continue through the
bounded model planner and must not be treated as deterministic unless a
a canonical route and evidence contract exist. The follow-up reliability pass
now uses one deterministic planner for blocking and streaming Chat, records
bounded prompt context as verifier evidence, preserves tool timeframe/session
metadata, rejects synthetic freshness timestamps, scopes numeric claims to
their named symbol where possible, and covers common market, indicator, and
portfolio phrasing.

## Private-flow smoke and latency decision

The sanctioned private-flow fixture in
`backend/tests/ai/test_phase_5_8_private_smoke.py` passes with synthetic
positions and journal entries. It patches deterministic price-history bars,
makes no model/provider calls, and verifies that private journal markers do
not appear in tool results, sanitized traces, or observability metadata.

The `500 ms` calculation-only target remains the hot steady-state target. Live
measurements were `5,539.949 ms` cold-process, `1,035.558 ms` first-warm, and
`4.291 ms` repeated-hot; the hot measurement passed. Release performance
checks must warm the process and SQLite/cache layer before evaluating this
target; the cold and first-warm values are accepted startup outliers.

## Final release gate

The Phase 5.8 release gate passed on 2026-09-23: no order-execution route is
registered, destructive actions remain confirmation-gated, the full backend
and frontend validation suites are green, the production frontend build
compiles, and all 19 provider-free evaluation cases pass in every category.
The four backend skips are environment-dependent Redis/loopback checks.

## Failure matrix

`backend/ai/evaluations/phase_5_8_failure_matrix.json` records the expected
fallback contract for provider outages, malformed replies, stale data, partial
symbol coverage, stream failure before the first chunk, and stream failure
after a chunk. Chat parsing is capped at two attempts; a stream that has
already emitted text is stopped rather than replayed through another provider.
