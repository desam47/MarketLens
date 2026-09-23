# Phase 5.8 Evaluation Report

**Harness versions:** verifier cases `5.8.0`, end-to-end Chat cases `5.8.5`
**Verifier version:** `5.8.1`
**Scope:** provider-free verifier, end-to-end Chat, and fallback-contract evaluation

There are two suites. The **verifier suite** (below, 19 cases) checks the
answer verifier on canned answer/trace pairs; its tool-choice and
clarification columns only check the canned trace, so they cannot fail on
their own and say nothing about how Chat routes a question. The
**end-to-end Chat suite** (added 2026-09-23) runs real conversations and is
the one that measures routing, tool choice, clarification, memory, and
action safety.

## End-to-end Chat suite

`backend/ai/evaluations/chat_runner.py` drives each case through
`answer_chat_message` or `stream_chat_message`. Ticker resolution,
deterministic and semantic routing, the bounded model planner, confirmation
gates, structured memory, and answer verification run unmodified. Only the
edges are scripted: a private in-memory database seeded per case, scripted
model replies (or a raised outage), scripted registry tool results
(`calculate` runs for real), a scripted screen, and fixture symbol context.
A model call with no scripted reply fails the case, so a question that
should route deterministically cannot silently fall through to the model.

Cases live in `backend/ai/evaluations/phase_5_8_chat_cases.json`. Run them
with `python -m backend.ai.evaluations.chat_runner`; pytest runs each case
in `backend/tests/ai/test_phase_5_8_chat_evaluation.py`. A category is
counted only for cases that set an expectation in it:

| Category | Passed | Applicable cases |
| --- | ---: | ---: |
| Correctness | 20 | 20 |
| Tool choice | 30 | 30 |
| Provenance | 5 | 5 |
| Clarification | 3 | 3 |
| Latency | 37 | 37 |
| Safety | 7 | 7 |

The 37 cases cover:

- **Calculations:** position risk from "Buy 200 AAPL at $220, stop $212"
  (plan matrix #1), a missing-input clarification, and the "use the same
  stop but 100 shares" follow-up (plan matrix #7).
- **Routing:** options (blocking and streaming), a two-symbol options
  clarification, comparison, pronoun follow-up, scanner, alerts, and
  portfolio risk.
- **Action safety:**
  - confirm-then-execute;
  - decline-then-"ok thanks" never executing;
  - a model trying to confirm itself;
  - additive alert creation;
  - a multi-step compound request.
- **Verification:** a verified grounded answer, a hallucinated price
  blocked, and an invented ticker blocked.
- **Failure modes:** a tool timeout, a model outage (blocking and
  streaming), and AI disabled.
- **Budgets:** a long compound request stopped by the planning budget.
- **Regeneration:** a mode applied as a server-side prompt section, with the
  question persisted unchanged; a timeframe/session rescope applied to the
  tool call for that turn only, with the remembered timeframe unchanged.
- **Sessions and timeframes:** remembered timeframe reuse and a
  premarket-scoped tool call.
- **Why did it move / news:** routing to `why_did_it_move` and `get_news`,
  and a clarification when no ticker is known.
- **Journal:** journal review routing, and a save that needs confirmation
  (ignoring the model's own confirmation) before the tool is called.
- **Dates and memory:**
  - "since last Friday" scopes `what_changed` to that day's close;
  - "yesterday" bounds the event timeline;
  - a named watchlist is remembered for a later "compare my watchlist".
  These cases run against a frozen New York clock.
- **User data:** signal-history routing, and saved scans honestly reported as
  browser-local.

When the harness was first run, it found two real gaps. There was no
deterministic path for plan matrix #1, and the calculator had no
quantity-based position-risk operation. The "same stop but 100 shares"
follow-up (matrix #7) did not reuse remembered inputs. Both were fixed
(`position_risk` calculation, labelled-input parsing, and follow-up
overrides). Temporarily reverting the confirmation-expiry fix makes
`declined_confirmation_never_executes_later` fail, which confirms the
suite catches regressions.

**Regression fixtures.** `python -m backend.ai.evaluations.export_fixtures`
turns approved `chat_regression_fixtures` rows into
`backend/ai/evaluations/regression/fixture_<id>.json` drafts in the same
case shape. Drafts start with `"needs_review": true` and are reported as
pending (not run) until a maintainer scripts their evidence and
expectations; reviewed files then run with the suite.

## Verifier suite categories

Each case is scored for correctness, tool choice, provenance, clarification
quality, latency-budget compliance, and action safety. Runtime latency and
provider-request measurements come from the persisted observability event;
provider-free cases use a zero external-latency budget.

## Verifier suite result

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

`backend/ai/evaluations/phase_5_8_failure_matrix.json` (version `5.8.1`)
records the expected fallback contract for provider outages, malformed
replies, stale data, partial symbol coverage, stream failure before the first
chunk, stream failure after a chunk, tool timeouts, and model outages. The
tool-timeout and model-outage rows name the end-to-end Chat case that
exercises them.

Every read-only and calculation tool call now has a hard deadline
(`AI_CHAT_TOOL_TIMEOUT_SECONDS`, default 20 s, or a per-tool
`ToolSpec.timeout_ms`). An overrun returns a failed result with
`failure_kind: "timeout"`, so the turn finishes with an explicit message.
The worker thread cannot be interrupted and finishes in the background.
Mutating tools are never given a deadline, so a write is never left in an
unknown state. Chat parsing is capped at two attempts; a stream that has
already emitted text is stopped rather than replayed through another provider.
