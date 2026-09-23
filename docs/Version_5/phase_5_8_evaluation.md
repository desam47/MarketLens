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

## Failure matrix

`backend/ai/evaluations/phase_5_8_failure_matrix.json` records the expected
fallback contract for provider outages, malformed replies, stale data, partial
symbol coverage, stream failure before the first chunk, and stream failure
after a chunk. Chat parsing is capped at two attempts; a stream that has
already emitted text is stopped rather than replayed through another provider.
