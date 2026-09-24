# Version 5 Chat Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (batch 12: `chat.py` split into six modules)
**Status:** All items and gaps complete. Batches 1 to 7 are committed (batch 3's migration is applied to the live DB); batch 8 (the four gaps) is committed too. Batch 9 (follow-ups, live checks, isolated e2e) is committed too; batch 10 (daily-close freshness) is committed too; batch 11 (natural reply wording) is committed too; batch 12 (the `chat.py` split) is committed too. Nothing is pushed.
**Scorecard:** 20 ✅ COMPLETE, 0 ⚠️ PARTIAL, 0 ❌ NOT STARTED, 0 🟡 DEFERRED.
**Source:** 2026-09-24 Chat review of `backend/ai/chat.py`, `backend/api/ai/chat_router.py`, `backend/repositories/chat_repository.py`, `frontend/src/components/ChatPanel.tsx`, and `frontend/src/services/api.ts`.
**Related:** [Phase audit](phase_audit_v5.md), [Version 5 plan](v5_plan.md)

"Verified" means the behaviour was reproduced with a throwaway probe test
run under the project's offline pytest guards (`-p backend.tests.conftest`).
"Code-read" means it follows from the code but was not reproduced.

Status legend (same as the phase audits):

- ✅ **COMPLETE** — fixed and covered by tests
- ⚠️ **PARTIAL** — the harmful behaviour is fixed, but specific gaps remain (listed in the entry)
- ❌ **NOT STARTED** — no change made yet
- 🟡 **DEFERRED** — intentionally postponed

Line numbers refer to the code as of batch 6. Each batch shifts
`chat.py` (batch 1 added about 40 lines near the top, batch 2 about 120
more, batch 4 about 30, batch 5 about 110; batch 6 moved the generation
code), so line numbers quoted in older notes or commits will not match.
Since batch 12, most functions named here live in sibling modules
(`chat_actions`, `chat_routing`, `chat_model`, `chat_intents`,
`chat_replies`); see "Splitting `chat.py`" under Enhancements.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| BF-01 | Critical | Backend | Affirmation prefix confirms destructive actions | Verified | ✅ COMPLETE |
| BF-02 | High | Backend | Position-size fallback maps numbers by order | Verified | ✅ COMPLETE |
| BF-03 | High | Backend | Date numbers become calculator inputs | Verified | ✅ COMPLETE |
| BF-04 | Medium | Backend | Market-metric questions dead-end in the calculator | Verified | ✅ COMPLETE |
| BF-05 | Medium | API | Positional argument misbinding in Chat router | Verified | ✅ COMPLETE |
| BF-06 | Medium | Backend | Destructive actions cannot be confirmed with AI off | Code-read | ✅ COMPLETE |
| BF-07 | Medium | Backend | Delete-alert confirmation does not name the alert | Code-read | ✅ COMPLETE |
| BF-08 | Medium | Backend | Stream shows unverified model text before verification | Verified | ✅ COMPLETE |
| BF-09 | Medium | Backend | Streaming and blocking reply paths have drifted | Verified | ✅ COMPLETE |
| BF-10 | Medium | Data | Message ids reused after Clear; feedback reattaches | Verified (live DB) | ✅ COMPLETE |
| BF-11 | Medium | Backend | Failed action leaves DB session unusable | Code-read | ✅ COMPLETE |
| BF-12 | Medium | Frontend | Nudge poll duplicates the user's message | Code-read | ✅ COMPLETE |
| BF-13 | Medium | Full stack | Stream timeout, disconnect, and resend gaps | Verified | ✅ COMPLETE |
| BF-14 | Low | Backend | Dotted tickers (BRK.B) rejected | Verified | ✅ COMPLETE |
| BF-15 | Low | Backend | Add-to-watchlist creates a new list for a mistyped or differently-cased name | Verified | ✅ COMPLETE |
| BF-16 | Low | Backend | Clarification questions recorded as completed steps | Code-read | ✅ COMPLETE |
| BF-17 | Low | Backend | `_SHARES_RE` slice leaves a literal `s*` | Code-read | ✅ COMPLETE |
| BF-18 | Low | Tests | Network guard does not block curl_cffi (Yahoo) | Verified | ✅ COMPLETE |
| BF-19 | Low | Frontend | Evidence card lost its 8-item cap; two ChatPanel tests failing | Verified | ✅ COMPLETE |
| BF-20 | Low | Tests | Two context tests expect an inferred "live" quote status | Verified | ✅ COMPLETE |

**Next:** nothing left in the tracker; the gaps (batch 8), the
follow-ups (batch 9), daily-close freshness (batch 10), reply wording
(batch 11) and the `chat.py` split (batch 12) are done. The full e2e
suite passed after batches 11 and 12 (31 of 31). What remains:
- **Push:** push `development` when you want it on `origin`.
- **Time-dependent test network use (new, open):** during market hours,
  `test_news_question_pulls_news` and `test_add_to_watchlist_end_to_end`
  run the real `why_did_it_move` / bars path. Provider availability
  checks and the live daily bar then try Finnhub, Alpaca and Yahoo. The
  guard blocks every attempt and both tests pass, but their results
  depend on the time of day. The same attempts happen on the pre-split
  code (checked on a clean checkout of `8f2d5c5` with the same `.env`),
  and the early-morning full-suite runs showed none. It also depends on
  test order: the full suite at 11:20 ET, in session, showed none, while
  runs of `backend/tests/ai` and `backend/tests/api` alone did. The fix is to patch
  the tool in those tests, or to make the provider availability checks
  offline under the guard.

Done in batch 9:
- **Checked in the running app** (headless Chromium via Playwright,
  2026-09-24):
  - Clear, then **Keep history**: the prompt showed, and the chat still
    had its 2 stored messages.
  - A question, then **Cancel**: the notice showed, the input stayed
    usable, and the saved reply appeared 28 s later through the reply
    poll, with no duplicate question.
  - "TSLA max drawdown this year": −33.95%, VERIFIED (see BF-04).
- **Follow-ups:** `$10k` account sizes (BF-02), class shares at Yahoo
  (BF-14), and `needs_input` for question steps (BF-16).
- **Isolated e2e:** `e2e/playwright.config.ts` now starts its own backend
  on 5002 and frontend on 3002, and never reuses the dev servers. The
  backend gets a fresh `.pytest_tmp/e2e/e2e.db`, migrated at startup,
  with Redis off. That matters because the live RQ workers write to the
  live database. It also gets its own log directory, and its CORS allows
  only the test frontend. Group A passed 4/4 this way, and the live chat
  kept 1 session and 6 messages.

Housekeeping done:
- **Phase audit counts (done 2026-09-24):** `phase_audit_v5.md` and
  `phase_audit_v5_tables.md` now say 45 tools and 56 actions. This doc
  first said 43 and 54, which counted only `get_price_statistics` and
  missed the two tools `9b95e77` added.
- **Batch 3 backup (deleted 2026-09-24):**
  `.pytest_tmp/bf10/pre_bf10_backup.db` (1.1 GB), after confirming the
  live database is on `20260930_chat_id_autoincrement` with both chat
  tables using `AUTOINCREMENT`.

---

## Critical

### BF-01 — Affirmation prefix confirms destructive actions

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `backend/ai/chat.py:557` (`_AFFIRM_INTENT`), used at `chat.py:3276` and `chat.py:3299` (`_finalize_parsed`), `chat.py:3188` (`_fallback_confirmation`) and `chat.py:2870` (`_confirm_pending_action`, BF-06).

`_AFFIRM_INTENT` only checked how the message starts. After a server
confirmation prompt, if the model returned `action="none"`, the stored
`pending_confirmation` was executed.

**Reproduced:** with a pending `delete_watchlist` for "Tech", these messages deleted it:

- "ok nevermind"
- "okay wait, actually don't"
- "sure, but first show me TSLA"

Only "no" was safe.

**Resolution:** `_AFFIRM_INTENT` now matches only when the whole message
is an affirmation. The affirmation can repeat itself ("ok ok"), add
"please" or "proceed", or restate the verb ("delete it", "remove that",
"save it"), and trailing `.`/`!` are allowed. Anything else is a decline,
and the pending action expires as before.

| Confirms | Does not confirm |
|---|---|
| "yes", "Yes!", "ok", "yes please", "yes, delete it", "go ahead.", "confirm" | "ok nevermind", "okay wait, actually don't", "sure, but first show me TSLA", "ok, what's the price of AAPL?", "yes?", "no" |

**Tests:** `backend/tests/ai/test_chat_actions.py::TestConfirmationAffirmation` (13 subtests).

**Follow-ups:**
- "yes?" is now a decline. That is deliberate, since a question is not
  consent.
- With AI off, "yes" still cannot confirm; see BF-06.

---

## High

### BF-02 — Position-size fallback maps numbers by order

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `_fallback_calculation`, `chat.py:683`.

**Reproduced:** "position size: risk 1% of my 10000 account, entry 50 stop 48"
produced `entry_price=1, stop_price=10000, account_value=50, risk_percent=48`.
The `risk_reward` fallback also used number order.

**Resolution:**

- **`position_size` and `risk_reward` fallbacks:** each input now comes
  from its own labelled phrase via `_position_risk_fields`. A missing
  label returns `None`, and Chat asks for the missing input instead of
  guessing.
- **New `_RISK_PERCENT_RE` (`chat.py:600`):** reads "risk 1%", "risking
  2%", "1% risk" and "1% of my account".
- **New `_ACCOUNT_BEFORE_RE` (`chat.py:597`):** reads a value written
  before its label ("my $10,000 account"). It is consulted only when the
  label-first `_ACCOUNT_RE` finds nothing. That keeps "target 60 account
  25,000" from reading 60 as the account size.
- **`position_risk` parsing:** gains the same account-before-label form.

The example now gives entry 50, stop 48, account 10000, risk 1%.

**Tests:** in `backend/tests/ai/test_chat_calculation.py`:
- `test_position_size_fallback_reads_labelled_inputs_not_number_order`
- `test_position_size_fallback_does_not_guess_a_missing_label`
- `test_risk_reward_fallback_reads_labelled_inputs`

**Follow-ups:**
- ~~A `k` suffix ("$10k account") is not parsed~~: fixed in batch 9. An
  account size now reads a `k`/`m` suffix (`_account_value`); prices
  don't, since "entry 10k" isn't realistic and a suffix there could
  misread a word.
- Inputs listed without labels ("entry, stop, target: 100, 95, 110") now
  get a clarifying question instead of a calculation.

### BF-03 — Date numbers become calculator inputs

**Status:** ✅ COMPLETE (2026-09-24, batch 1; completed in batch 5)
**Where:** `_fallback_calculation`, `chat.py:683`; the new `_CALC_DATE_RE` at `chat.py:608`.

**Reproduced:** "What was NVDA's return from Jan 5 to Jan 20?" produced
`percentage_change(old=5, new=20)`, i.e. +300%.

**Completed in batch 5:** batch 1 removed the wrong answer but left the
question unanswered (⚠️ PARTIAL). The price-history return tool from BF-04
now answers it: "What was NVDA's return from Jan 5 to Jan 20?" routes to
`get_price_statistics` with `start=2026-01-05`, `end=2026-01-20`.

**Resolution:** `_fallback_calculation` returns `None` when the message
contains a calendar date. That covers a month name followed by a day
("Jan 5", "January 20, 2026"), `M/D` or `M/D/Y`, and ISO `YYYY-MM-DD`.
The date's numbers never reach the calculator. The position-risk path
runs before this check, so a trade description that mentions a date
still parses.

This differs from the fix first suggested. Stripping dates and computing
from what was left would still answer a market question with arithmetic,
so the fallback now steps aside entirely.

**Tests:** in `backend/tests/ai/test_chat_calculation.py`:
- `test_date_numbers_are_not_calculator_inputs`
- `test_plain_percent_change_still_uses_the_fallback`

**Follow-ups:**
- **Date-range answers:** now given by BF-04's tool.
- **Bare years:** the calculator fallback still doesn't treat "2024 to
  2025" as dates, because `from 2000 to 2500` can be real prices. With a
  ticker, BF-04's parser reads it as a year range.
- **"may" false positive:** "may" is matched as a month when followed by
  a number ("may 5"). This is unlikely in calculator wording but possible.

---

## Medium

### BF-04 — Market-metric questions dead-end in the calculator

**Status:** ✅ COMPLETE (2026-09-24, batch 5)
**Where:** new tool `get_price_statistics` (`backend/ai/market_tools.py:1281`, request model at `:59`); new parser `backend/ai/price_metric_intent.py`; route at the top of `_build_deterministic_chat_reply` (`chat.py:2464`); reply formatter `_format_price_statistics_reply` (`chat.py:5199`); calculator op `return_correlation` (`calculator.py:278`); `_REUSE_MEMORY_HINT` (`chat.py:579`).

**Reproduced (before the fix):**

- "What is TSLA's max drawdown this year?", "What's AAPL volatility over
  30 days?" and "What's the correlation between AAPL and MSFT?" all
  replied "What values should I use for that calculation?"
- "what's the return on it" silently re-ran the remembered
  `position_risk` calculation with stale inputs.
- "percent change for AAPL from 2024 to 2025" routed to today's 1d
  `change_percent`.

**Resolution:**

- **New read-only tool, `get_price_statistics`:** one statistic from a
  symbol's regular-session daily closes over an explicit window: start/end
  dates, `lookback_days`, or a per-metric default (volatility 30 days,
  drawdown and correlation 365).
  - **Metrics:**
    - `return_percent`: first to last close in the window.
    - `volatility`: daily standard deviation of returns, plus annualized
      (× √252).
    - `max_drawdown`: with the peak and trough closes and their dates.
    - `correlation`: of daily returns, on the dates both symbols traded.
  - **No model arithmetic:** every number comes from the calculator, and
    the payload carries its formulas and assumptions.
  - **Supported ranges only:** bars come through the same path as
    `get_bars`, using the smallest provider range that covers the window.
    An unknown range string would silently fetch 3 months. The tool then
    trims the bars to the exact dates.
  - **Honest coverage:** the payload states the dates and number of closes
    actually used, and reports a coverage gap when the provider's history
    starts after, or ends before, the requested window.
- **Returns-based correlation:** a new calculator operation,
  `return_correlation`, correlates daily returns. The existing
  `correlation` correlates price levels, which makes any two trending
  stocks look highly correlated.
- **Parser, `price_metric_intent.py`:**
  - **What it reads:** the metric, one or two tickers ("with the market"
    uses SPY), and the window:
    - date ranges ("from Jan 5 to Jan 20", "since Mar 3");
    - year ranges and single years ("from 2024 to 2025", "in 2024");
    - "YTD" / "this year", "last year" (the previous calendar year) and
      "over the last year" (trailing 365 days);
    - "N days/weeks/months/years" and "the last week/month/quarter".
  - **Dates without a year:** a date without a year is the most recent one
    not in the future.
  - **Clarifying instead of guessing:** it asks when the window is
    unreadable or in the future, or when a correlation lacks a second
    symbol.
  - **When it steps aside:**
    - the message has its own price inputs (a calculator question);
    - it asks for implied volatility (options);
    - it names several tickers for a non-correlation metric (a
      comparison).
- **Chat route:** the route runs before the semantic route and the
  calculator hint.
  - **Skipped for other intents:** multi-step, why-did-it-move, anomaly,
    scenario, historical P&L and watchlist messages.
  - **Deferral:** trailing-window returns that the semantic route already
    answers ("over the last week", "this month") keep that route.
  - **Newly answered:** "over the last 5 days" and "over the last year"
    returns had no route and now get this tool.
- **Re-running a calculation needs an explicit reference:**
  `_REUSE_MEMORY_HINT` requires "previous/same/those/last values, inputs,
  numbers or calculation", or "again"/"re-run"/"recalculate". A bare "it"
  or "last" no longer re-runs the remembered calculation.
- **Registration:** the tool is registered and added to
  `_MARKET_TOOL_ACTIONS` and `ChatReplyResponse.action`. It is also
  documented in the model's tool list, so the model can select it too.
  The pinned registry list in `test_tool_registry.py` was updated.

**Tests:** `backend/tests/ai/test_price_statistics.py` (37 tests):
- **Calculator:** the returns-vs-levels correlation and its input checks.
- **Tool:** return between dates, volatility and range choice, drawdown
  peak/trough, correlation on shared days, coverage gaps, too-few-closes,
  request validation, and range selection.
- **Parser:** 12 phrasings read correctly, 7 where it steps aside, and 4
  where it asks.
- **Chat routing:** routing, deferral to the semantic route, and the bare
  "it" no longer re-running the last calculation.
- **End to end:** a drawdown turn that the answer verifier marks
  `verified`.

Removing the route fails 3 tests; restoring the old reuse wording fails 1.

**Follow-ups:**
- **Stale counts (fixed 2026-09-24):** the registry now has 45 tools and
  `ChatReplyResponse.action` accepts 56 actions, so the counts in
  `phase_audit_v5.md` ("42 tools", "53 actions") were out of date. This
  entry first said 43 and 54, missing the two tools `9b95e77` added.
- **Label mismatch:** "last week" / "last month" mean a trailing 7 or 30
  days, while "last year" means the previous calendar year. That matches
  common trader usage, and the reply shows the exact dates used.
- **Timestamp convention (checked live, batch 9):** bar dates are read
  from the timestamp's first 10 characters, the same convention
  `get_session_stats` uses. Against Webull on 2026-09-24, "TSLA max
  drawdown this year" used 2026-01-02 to 2026-09-23 (182 closes), the
  right trading days, so no date shifted.
- **Live data (checked, batch 9):** the same question in the running app
  gave −33.95% ($451.67 on Jan 5 to $298.32 on Jul 29), marked VERIFIED.
- **Evidence said STALE for an up-to-date close (fixed in batch 10):** in
  that reply the Evidence badge said STALE and the tool pill "124095s
  old". Yesterday's close is the newest complete daily close, but its age
  was counted from the daily bar's midnight timestamp against a flat
  15-minute limit. See "Daily-close freshness" under Gaps.
- **Today's forming bar counted as a close (fixed in batch 10):** found
  while re-checking live. Once the session opened, the daily feed carried
  a bar for today built from live 1-minute data (`live_from_1m`), and the
  tool counted it: the same question then covered "2026-01-02 to
  2026-09-24, 183 daily closes". The window now ends at the latest
  completed session.

### BF-05 — Positional argument misbinding in Chat router

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `send_message` and `send_message_stream` in `backend/api/ai/chat_router.py`; the new `_turn_kwargs` at `chat_router.py:780`.

**Reproduced (before the fix):**

- `{regeneration_timeframe: "1h"}` alone reached `answer_chat_message` as
  `chart_state={"timeframe": "1h", "session": None}`.
- `chart_state` plus `regeneration_session` put the scope dict into
  `regeneration_mode`.

The UI always sent a mode with a scope, so only API callers were affected.

**Resolution:** both endpoints now call
`answer_chat_message(session_id, content, **_turn_kwargs(payload))` and
`stream_chat_message(...)` the same way. `_turn_kwargs` names every
optional argument and includes it only when the client sent it. This
replaces two hand-built positional argument lists; the streaming one had
a separate branch for the no-extras case.

**Tests:** in `backend/tests/api/test_chat_router.py`:
- New: `test_scope_without_mode_is_not_passed_as_chart_state`.
- Updated: six existing assertions changed from positional (`args[2]` to
  `args[5]`, `assert_called_once_with(1, "...", None)`) to keyword
  arguments. They pinned the old call shape.

### BF-06 — Destructive actions cannot be confirmed with AI off

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where (before the fix):** the confirmation replay lived only in
`_finalize_parsed`, which runs only after a model reply. With AI off,
"yes" went to `_deterministic_context_reply`, and
`_expire_carried_confirmation` dropped the pending action.

**Resolution:** a new `_confirm_pending_action` (`chat.py:2849`) runs
first in both `_generate_reply` and `_generate_reply_streaming`. That is
before intent routing, the model, the AI-off fallback and the legacy
"no data" degrade. When the session has a pending confirmation and the
message is a strict affirmation (the BF-01 `_AFFIRM_INTENT`), it replays
the stored action through `_run_turn_actions`. `_finalize_parsed` still
enforces the gate and replays the stored payload.

Side effect: a "yes" to a pending action no longer costs a model call
when AI is on either.

**Tests:** in `backend/tests/ai/test_chat_actions.py::TestEndToEnd`:
- `test_yes_confirms_with_ai_off`
- `test_yes_confirms_on_the_streaming_path_without_a_model_call`
- `test_decline_with_ai_off_keeps_the_watchlist`

The first two fail with the fix removed.

### BF-07 — Delete-alert confirmation does not name the alert

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** `_destructive_target_problem` (`chat.py:4044`), `_confirm_prompt` (`chat.py:4079`), `_delete_watchlist` (`chat.py:4322`), called from `_finalize_parsed` (`chat.py:3200`).

The prompt was "Delete that alert? Say yes to confirm." The target id is
model-chosen, so the trader confirmed without seeing what would be
deleted. The pending `delete_watchlist` stored the name, not the list
resolved at prompt time.

**Resolution:** before a destructive action gets a confirmation question,
`_destructive_target_problem` resolves its target.

- **`delete_alert`:**
  - No id: Chat asks "Which alert should I delete?".
  - Unknown id: "I couldn't find that alert — it may already be deleted."
  - Existing alert: the prompt names it, e.g. `Delete the alert
    "Breakout" (AAPL price above 200)? Say yes to confirm.`
- **`delete_watchlist`:**
  - Ambiguous or unknown: Chat answers directly and stores no pending
    confirmation. Previously the ambiguity question left a pending action
    with no target, which "yes" could replay.
  - Resolved: the list's id is pinned on the pending confirmation, and
    `_delete_watchlist` deletes by that id. A list created or renamed
    between the question and "yes" cannot change the target.

Any of these answers also clears a carried pending confirmation.

**Tests:** `backend/tests/ai/test_chat_actions.py::TestDestructiveTargetResolution` (6 tests; 5 fail with the target check removed).

**Follow-up:** `remove_from_watchlist` still stores the watchlist name, not
an id. Its prompt already names the symbol and list, so it was left as is.

### BF-08 — Stream shows unverified model text before verification

**Status:** ✅ COMPLETE (2026-09-24, batch 6; confirmed in batch 7)
**Where:** `_holds_streamed_text` (`chat.py:2973`), `_stream_and_parse` (`chat.py:3096`), the one-shot branch of `_reply_events` (`chat.py:3204`).

Deltas were the model's raw `reply` field, streamed before
`verify_answer` and before any action ran. The note at `chat.py:544-551`
records that the model sometimes writes a fake "Done — deleted…"; that
text was visible until the `final` frame overwrote it.

**Resolution (backend):**

- **Held for action-like requests:** no model text is streamed when the
  message looks like a request the app answers with its own text. That
  covers alerts, watchlist changes, delete/remove/save/create/cancel
  wording, calculations, a saved assumption, and a "yes" to a
  confirmation. The final frame still carries the answer; only the live
  typing is lost for those turns.
- **Stopped when an action shows up:** streaming also stops for the rest
  of a model attempt as soon as its JSON shows an `action` other than
  `none`, or `wants_reanalysis: true`.
- **One-shot providers:** with `chat_streaming` off, the single delta is
  sent only after parsing shows the reply is the model's own answer
  (action `none`, no reanalysis).
- **Prompt unchanged:** the prompt was left alone. It lists `"reply"`
  first, so the action check mostly catches models that write `"action"`
  first. Reordering the prompt's fields would change what the model is
  asked for, which couldn't be tested against the live model.

**Frontend: already in place (correction).** The original entry missed
that streamed text was already shown as a draft. Commit `f24b4cf`
(2026-09-23, the day before this review) gives it a dashed border and the
label "Unverified draft — numbers are checked before this answer is
final." until the final frame replaces it (`ChatPanel.tsx`, covered by
`labels streamed text as an unverified draft until the final message
arrives`). So once batch 6 held back action turns, nothing was left:
- ordinary answers stream as a labeled draft;
- the text of an action turn isn't streamed at all.

Batch 7 changed only what a broken stream's partial draft says. It no
longer advises "Retry", which would repeat the turn (see BF-13).

**Tests:** in `backend/tests/ai/test_chat_reply_paths.py::TestStreamedTextGate`:
- `test_an_action_request_streams_no_model_text`
- `test_an_action_seen_early_in_the_json_stops_the_stream`
- `test_non_streaming_provider_emits_no_delta_for_an_action`

All three failed on the old code. Disabling the intent hold, or the
early-action check, each fails its test.

### BF-09 — Streaming and blocking reply paths have drifted

**Status:** ✅ COMPLETE (2026-09-24, batch 6)
**Where:** `_reply_events` (`chat.py:3204`), with `_reply_without_model` (`chat.py:2999`) and `_stream_and_parse` (`chat.py:3096`); `_generate_reply` (`chat.py:3305`) and `_generate_reply_streaming` (`chat.py:3936`) are now thin wrappers.

The generation half of a turn existed twice, and the copies had drifted:

- **No-data watchlist exemption:** the streaming copy of the legacy "no
  data" degrade lacked the watchlist-intent exemption the blocking copy
  had. In a single-ticker session for a ticker with no data, streaming
  refused "add it to my watchlist".
- **Parse failures:** streaming answered with `extractor.text`, the raw
  model text decoded from the broken JSON. Blocking answered "I couldn't
  process that — could you rephrase?"
- **No text from the model:** streaming said "AI is currently
  unavailable…", blocking "Something went wrong reaching the AI
  provider…".
- **Time budget:** blocking passed `started_at` to the deterministic
  short-circuit; streaming did not.

**Resolution:** one generator, `_reply_events`, now runs the whole
generation half for both transports.
- **One routing path:** confirmations, clarifications, no-data answers,
  deterministic routes and the AI-off fallback are one function,
  `_reply_without_model`, using the blocking copy's behaviour (with the
  watchlist exemption).
- **One model call:** the model is called either as one completion (the
  existing `_complete_and_parse`) or streamed through `_stream_and_parse`.
  The streamed version makes the same attempts and trace records and
  returns the same failure reasons.
- **One wording per failure:** `_MODEL_FAILURE_REPLIES` gives one reply
  per failure, whichever transport ran the turn.
- **Same time budget:** both transports pass the same `started_at`.
- **Wrappers kept:** `_generate_reply` drains the generator and
  `_generate_reply_streaming` yields from it. Both names stay because
  tests call and patch them.

With batch 2's shared `_finish_turn`, the two transports now differ only
in whether the model call streams.

**Tests:** in `backend/tests/ai/test_chat_reply_paths.py`:
- `test_model_failures_read_the_same_on_both_paths` runs the same model
  outcome through both paths, with streaming providers on and off, and
  requires identical results (6 cases).
- `TestLegacyNoDataTurnWatchlistAdd`.

The four drifted cases failed on the old code. One existing test built a
stand-in `_Turn` without `capped`; `capped=False` was added to it.

### BF-10 — Message ids reused after Clear; feedback reattaches

**Status:** ✅ COMPLETE (2026-09-24, batch 3)
**Where:** migration `alembic/versions/20260930_chat_id_autoincrement.py`; `ChatSession` / `ChatMessage` in `backend/models/chat.py`; `ChatRepository.delete_sessions` in `backend/repositories/chat_repository.py`.

`delete_sessions` removed messages and sessions but not `chat_feedback`,
`chat_regression_fixtures` or notebook items that reference message ids.
The chat tables had no `AUTOINCREMENT`, so SQLite reused freed ids. The
live DB showed `count(chat_messages)=2, max(id)=2` after a clear.

**Impact:**
- **Feedback:** old ratings could appear on new messages.
- **Fixtures:** promoting a new message could hit `UNIQUE(message_id)`
  and fail with a 500.
- **Notebooks (found while fixing):** `save_notebook_item` de-duplicates
  by `(notebook_id, message_id)`. Saving a new answer whose id was reused
  would silently overwrite an older saved item with a different answer.
  The browser-local notebook merge also keys by message id.

**Resolution:**

- **Migration `20260930_chat_id_autoincrement`:**
  - Deletes feedback whose message no longer exists.
  - Rebuilds `chat_sessions` and `chat_messages` with `AUTOINCREMENT`,
    using Alembic batch mode. Rows, defaults, the foreign key and all six
    indexes are kept.
  - Seeds `sqlite_sequence` so the next message id is above every id
    still referenced anywhere: surviving messages, feedback, fixtures and
    notebook items.
  - Does nothing on non-SQLite databases.
  - The downgrade rebuilds the tables without `AUTOINCREMENT`. Deleted
    orphan feedback is not restored.
- **Models:** `ChatSession` and `ChatMessage` declare
  `sqlite_autoincrement=True`, so tables created by `create_all` (tests)
  match.
- **`delete_sessions`:** deletes those messages' feedback along with them.
- **Decision — regression fixtures and notebook items survive a Clear.**
  - Why: they are self-contained copies (prompt/response/blocks, or
    question/answer/blocks), and fixtures are deliberate regression data.
    They already survived a Clear before this change.
  - Their `message_id` now points at a deleted message and can never
    match a new one.
  - `PRAGMA foreign_key_check` reports such fixtures. That is expected,
    since this app does not enable foreign-key enforcement.

**How it was applied:**
- **Draft outside the project:** the migration was written and tested in
  a scratch copy of `alembic/`, so the dev server's `--reload` could not
  pick it up early.
- **Tested on a copy:** it was validated on a copy of `marketlens.db`
  under `.pytest_tmp/bf10/`. Seeded rows were kept, orphan feedback was
  removed, the next id was 73 above a notebook reference of 72, and
  downgrade and re-upgrade were clean.
- **Backup:** `.pytest_tmp/bf10/pre_bf10_backup.db` (1.1 GB, passes
  `quick_check`, revision `20260929_chat_notebooks_fixtures`). Delete it
  once you're satisfied.
- **Live migration:** adding the file to `alembic/versions/` triggered
  the reload. The live DB upgraded at 2026-09-24 07:23 UTC, and the
  server came back healthy (`/api/health` 200, chat session endpoint 200).
- **Live state after:** both tables use `AUTOINCREMENT`, the 1 existing
  session is intact, and all 6 indexes are present.

**Tests:**
- `backend/tests/migrations/test_chat_id_autoincrement_migration.py`
  (2 tests: upgrade on seeded "cleared chat" data; downgrade keeps rows).
- `backend/tests/repositories/test_chat_repository.py`:
  - `test_delete_sessions_removes_feedback_but_keeps_fixtures_and_notebook_items`
  - `test_ids_are_not_reused_after_a_clear`, which fails without
    `sqlite_autoincrement`.

**Follow-ups:**
- `chat_feedback`, `chat_regression_fixtures` and the notebook tables
  still use plain rowids. Their own ids are not referenced elsewhere, so
  reuse there is harmless.
- **SQLite gotchas met while validating:**
  - Alembic reports "non-transactional DDL", so a failure partway through
    a migration does not roll back. Back up first.
  - A `.backup` copy of this WAL-mode DB cannot be opened with
    `sqlite3 -readonly` (it can't create the `-shm` file). Open the copy
    normally.

### BF-11 — Failed action leaves DB session unusable

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** `_rollback_quietly` (`chat.py:2062`), `_finish_turn` (`chat.py:2079`), `answer_chat_message` (`chat.py:1982`), `_run_action` (`chat.py:5494`).

The exception was caught but `db.rollback()` was never called. If a flush
failed (e.g. `IntegrityError`), the next write on the same session raised
`PendingRollbackError`. The stream path's finalization fallback used the
same session, so no assistant row was saved. `answer_chat_message` had no
guard after the user message was saved, so any failure became a 500.

**Resolution:**

- **Rollback on failure:** the new `_rollback_quietly(db)` runs in
  `_run_action`'s `except`, in `_run_screen`'s `except`, and after a
  generation or finalization failure in both transports. Earlier steps
  commit their own work, so only the failed step's partial changes are
  discarded.
- **Shared finish:** verification, response blocks, planner-state
  persistence and the assistant row now live in one `_finish_turn` used
  by both `answer_chat_message` and `stream_chat_message`. It always
  persists exactly one assistant row. If finishing fails, it rolls back
  and stores "I couldn't finish verifying that answer…".
- **Blocking generation guard:** `answer_chat_message` now catches a
  generation exception like the stream path does and stores
  "Something went wrong answering that — please try again."
- **Stream wording:** the stream's generation-exception reply changed
  from "…reaching the AI provider…" to the same neutral wording, because
  the exception is not necessarily the provider's.
  - Provider failures handled inside generation keep their own wording.

**Tests:**
- `backend/tests/ai/test_chat_actions.py::TestRunActionNeverRaises::test_failed_flush_is_rolled_back_so_the_session_stays_usable`
  fails with `PendingRollbackError` without the rollback.
- `backend/tests/ai/test_chat.py::TestTurnFailureStillPersistsOneReply`
  (3 tests: blocking generation, blocking finalization, streaming
  generation).

### BF-12 — Nudge poll duplicates the user's message

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `frontend/src/components/ChatPanel.tsx` — the new `mergePolledMessages` at `ChatPanel.tsx:69`, called from the nudge poll at `ChatPanel.tsx:327`.

After a turn, only the placeholder was replaced by the server message.
The optimistic user message kept its negative id, so the next 20 s poll
saw the real user row (positive id) as new and appended it after the
assistant reply.

**Resolution:** the poll merge is now the exported `mergePolledMessages`
helper. For each server row not already in state:

- **User rows:** a user row whose content matches a local optimistic
  (negative-id) user message replaces that message in place.
- **Everything else:** it is appended as before, so proactive nudges
  still arrive.

When nothing changed, the helper returns the previous array, so React
skips the re-render.

This takes the "merge by id" option rather than reloading the transcript
after `final`. A reload would drop the transient `tools` trace and
provenance, which only the live response carries.

**Tests:** `frontend/src/components/ChatPanel.test.tsx` › `mergePolledMessages (BF-12)` (3 tests).

**Follow-ups:**
- If the trader sends the same text twice before a poll, the first
  server row replaces the first optimistic copy and the second replaces
  the second, so both stay single.
- Alert-scoped sessions skip the 20-second poll, so the original bug
  never reached them. Since batch 7 they do poll, every 4 s, while
  waiting for a reply after Cancel, a timeout or a dropped stream
  (BF-13); that poll merges through the same `mergePolledMessages`.

### BF-13 — Stream timeout, disconnect, and resend gaps

**Status:** ✅ COMPLETE (2026-09-24; backend in batch 6, frontend in batch 7)
**Where:**
- **Backend:** the stream drain in `send_message_stream`
  (`chat_router.py:867`); `_prepare_turn` (`chat.py:1171`, the user row
  is saved at `:1402`).
- **Frontend:** `streamChatMessage` (`frontend/src/services/api.ts:2757`).
  In `ChatPanel.tsx`: the stream ref (`:187`), the teardown abort
  (`:293`), `awaitServerReply` (`:390`), the Cancel button (`:811`) and
  `replyArrived` (`:70`).

- **No timeout (fixed, frontend):** the stream had no timeout or
  `AbortController`. A hung stream leaves `sending=true` and the input
  locked; unmounting does not cancel.
- **Disconnect (fixed):** on client disconnect the drain loop broke and
  abandoned the turn generator. The user message was saved with no
  assistant reply, and actions could be half-done.
- **Resend (fixed):** an exception in `_prepare_turn` after the user row
  was saved, but before `meta`, was reported as `started=false`. The
  client then fell back to the blocking endpoint and saved the user
  message twice.

**Resolution (backend):**

- **Disconnect:** after a disconnect the router's drain keeps running the
  turn to the end without sending frames, so the reply is saved, and any
  action already under way finishes and is recorded. A failed frame hand-off also
  marks the client as gone, instead of abandoning the turn.
- **Resend:** `_prepare_turn` now saves the user message last, after
  context assembly and the planner-state write. A failure before `meta`
  leaves nothing saved, so the client's resend is not a duplicate.
- **Transcript unchanged:** the transcript is built from the prior
  messages exactly as before, because the new message isn't in the
  history it reads.
- **A leftover message:** a reply finished after a disconnect appears on
  the next load of the conversation or via the 20-second message poll.

**Resolution (frontend, batch 7):**

- **Timeout:** `streamChatMessage` abandons a stream that sends nothing
  for `AI_TIMEOUT_MS` (150 s, the non-streaming call's limit). The idle
  timer restarts on every chunk, so a slow but live reply is never cut
  off.
- **Cancel:** a "Cancel" button (accessible name "Cancel reply") shows
  while a reply streams. The panel also cancels when you switch session
  or leave, and then updates nothing from the old turn but still unlocks
  the input.
- **Failure flags:** each way of stopping is flagged so the panel can act
  on it: `aborted`, `timedOut`, `turnStarted`, and `serverFailed` (an
  error the server reported after the turn started).
- **No resend:** a cancelled, timed-out or broken stream is never resent,
  because the server finishes the turn anyway (batch 6), so a resend
  would repeat any action. Instead the panel:
  1. shows what the server has stored;
  2. checks for the reply every 4 s for up to 2 minutes, in alert chats
     too, which don't have the 20-second refresh;
  3. replaces a broken stream's partial draft with the saved reply when
     it arrives.
- **If the message never arrived:** if the wait runs out and the server
  never saved the message, the panel removes it and says so ("never
  reached the server — please send it again").
- **Server-reported failures:** an error the server itself reported
  after the turn started shows as an error, and the panel doesn't wait
  for a reply.
- **Non-streaming fallback kept:** a stream that never started still
  falls back to the non-streaming endpoint. That's safe since batch 6,
  because the server saves nothing before the turn starts.
- **Honest wording:** Cancel means "stop waiting", and its tooltip and
  notice say the server still finishes the reply. The server can't be
  told to stop mid-turn, because that could leave an action half-done.

**Tests (frontend, batch 7):**
- **Stream client:** `frontend/src/services/api.test.ts` ›
  `streamChatMessage stopping (BF-13)` (5 tests): idle timeout, a slow
  but live stream kept alive, caller abort before and after `meta`, and
  a server-reported failure.
- **Chat panel:** `frontend/src/components/ChatPanel.test.tsx` ›
  `stopping and recovering a streamed turn (BF-13)` (6 tests): Cancel
  with no resend and the saved reply appearing, timeout, a partial draft
  replaced by the saved reply, a message that never reached the server,
  unmount, and session switch. Plus `replyArrived` (1 test).
- **Mutations:** each was re-run with one change removed, and its tests
  fail:
  - the per-chunk timer restart;
  - the abort flags;
  - `serverFailed`;
  - the teardown abort;
  - no-resend-on-cancel;
  - waiting for the reply.

**Tests (backend, batch 6):** in `backend/tests/ai/test_chat_reply_paths.py`:
- `test_a_client_disconnect_does_not_abandon_the_turn` drives the real
  router endpoint and cancels the consumer mid-turn, as Starlette does on
  disconnect.
- `TestTurnStartAndDisconnect`:
  - `test_a_failure_before_the_reply_starts_persists_nothing`
  - `test_the_user_row_exists_by_meta_and_the_transcript_stays_in_order`

Restoring the old `break`, or saving the user row first again, each fails
its test.

---

## Low

### BF-14 — Dotted tickers rejected

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `backend/ai/chat_symbols.py` — `_RE_CASHTAG` (`:290`), `_RE_BARE` (`:294`), `_canonical` (`:424`).

"price of BRK.B" resolved to the rejected symbol `BRK` (verified). Even
`$BRK.B` matched its suffix and then dropped it.

**Resolution:** a share-class suffix (`.B`, `-B`, one or two letters) is
now part of the ticker in cashtags, bare upper-case tickers, `ticker:`
mentions and the known-ticker sweep. `_canonical` writes it in the dot
form (`BRK-B` → `BRK.B`), matching `_NAME_TO_TICKER`'s `"berkshire":
"BRK.B"`. Sentence punctuation ("I like AAPL. MSFT…") and hyphenated pairs
("SPY-QQQ spread") still split into separate tickers.

**Tests:** in `backend/tests/ai/test_chat_symbols.py`:
- `test_share_class_suffix_is_part_of_the_ticker`
- `test_share_class_suffix_does_not_swallow_words_or_sentence_ends`
- `test_unresolved_dotted_ticker_is_reported_whole`

Two of the three fail on the old code.

**Follow-up (fixed in batch 9):** Yahoo did miss class shares. Asked
live on 2026-09-24, its chart API answered `BRK.B` with "No data found"
and `BRK-B` with a price. The Yahoo provider now asks for the dash form
(`_yahoo_symbol` in `yfinance_provider.py`) and still returns `BRK.B`.
Only the one-letter class form is translated, so exchange suffixes like
`SHOP.TO` pass through. Tests: `test_class_shares_are_asked_for_in_yahoo_form_and_returned_in_app_form`
and `TestYahooSymbolForm` in `backend/tests/market_data/test_yfinance_provider.py`.

### BF-15 — Add-to-watchlist creates a new list for a mistyped or differently-cased name

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_find_watchlist_by_name` (`chat.py:4151`), `_add_to_watchlist` (`chat.py:4275`), `_unknown_watchlist_reply` (`chat.py:4299`), the add route in `_build_deterministic_chat_reply` (`chat.py:2774`).

**Reproduced** (end-to-end probe, AI off, an existing list "Tech"):

| Message | Reply | Lists afterwards |
|---|---|---|
| "add RIVN to my tech watchlist" | "Done — added RIVN to tech." | `Tech` and a new `tech` |
| "add RIVN to my Tehc watchlist" | "Done — added RIVN to Tehc." | a new `Tehc` too |

Watchlist names were matched case-sensitively everywhere in Chat
(add, remove, delete, create's duplicate check).

**Correction to the original entry:** it said unresolvable tickers were
added (and backfilled) through the deterministic path. The probe showed
"add XYZQ to my watchlist" is already refused ("I couldn't find current
verified market data for XYZQ…") by an earlier rejected-symbol check.
No change was needed there.

**Resolution:**

- **Case-insensitive names:** `_find_watchlist_by_name` tries the exact
  name and then a case-insensitive match. `_resolve_watchlist` uses it, so
  add, remove, delete and the delete confirmation all find "Tech" from
  "tech". `_create_watchlist`'s duplicate check uses it too.
- **No stray lists:** when a named list doesn't exist and the trader has
  other lists, `_add_to_watchlist` no longer creates it.
  - Close match: 'I couldn't find a watchlist called "Tehc". Did you mean
    "Tech"?' (via `difflib`).
  - No close match: it lists the real watchlists and says how to create
    one.
  - With no watchlists at all, it still creates the named (or default)
    list.
- **"add RIVN to a new watchlist called Momentum"** now routes to
  `create_watchlist` with the ticker, so an explicit new list still works.

**Tests:** in `backend/tests/ai/test_chat_actions.py`:
- Five `TestActionHandlers` tests: case-insensitive match, typo
  suggestion, unrelated name, create-when-none, case-insensitive
  duplicate.
- `TestWatchlistAddRouting` (2 tests).

Removing the case-insensitive lookup or the new-watchlist routing makes
its test fail.

### BF-16 — Clarification questions recorded as completed steps

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** the ambiguity replies in `_add_to_watchlist`, `_remove_from_watchlist`, `_delete_watchlist`, `_set_entity_type`; the chain in `_run_turn_actions` (`chat.py:3388`).

The "which watchlist?" replies returned `grounded=True`.
`_run_turn_actions` logged them as `completed` and a multi-step chain
carried on, so "add RIVN to my watchlist, then alert me above 20" could
create the alert while the add was still waiting for an answer.

**Resolution:**
- **Not completed:** the four ambiguity replies return `grounded=False`,
  including `_set_entity_type`'s, which the original entry missed.
- **Chain stops:** `_run_turn_actions` no longer chains after a first
  step that failed or asked a question. It also stops after a later step
  that did (`chat.py:3643`). Later steps often depend on the earlier one
  ("create X and add Y to it").
- **Updated tests:** two existing tests that pinned `grounded=True` for
  these questions were changed to expect `False`.

**Tests:** in `backend/tests/ai/test_chat_actions.py::TestRunTurnActions`:
- `test_no_chain_after_a_first_step_that_asked_a_question`
- `test_chain_stops_after_a_later_step_that_asked_a_question`

Both fail with their stop removed.

**Follow-up (fixed in batch 9):** a clarification step used to show as
`failed` (⚠). An ungrounded step whose reply ends in a question now
records `needs_input` (`_asks_for_input`) and shows "?"
(`stepIcon` in `ChatPanel.tsx`). The handlers' `(text, grounded)` return
is unchanged: a question is recognised by its "?", which only affects
the label, since the chain already stops on any ungrounded step. Tests:
the two BF-16 chain tests now check the status, plus
`test_a_step_that_failed_without_a_question_stays_failed` and
`stepIcon (BF-16 follow-up)`.

### BF-17 — `_SHARES_RE` slice leaves a literal `s*`

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_BARE_NUM` / `_NUM` / `_SHARES_RE`, `chat.py:583-588`.

`_NUM[4:]` stripped `\$?\` but left `s*`, so the pattern contained "zero
or more literal `s`" ("buy s200 AAPL" read as 200 shares).

**Resolution:** a named `_BARE_NUM` pattern; `_NUM` is `\$?\s*` + `_BARE_NUM`, and
`_SHARES_RE` uses `_BARE_NUM` directly. No slicing.

**Tests:** `backend/tests/ai/test_chat_actions.py::TestPositionRiskParsing::test_share_count_pattern_has_no_stray_literal`.

### BF-18 — Network guard does not block curl_cffi (Yahoo)

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `_block_curl_cffi` in `backend/tests/conftest.py:255`, with the shared `_refuse_network_attempt` / `_LOOPBACK_HOSTS`.

`backend/tests/conftest.py` patched only `socket.socket.connect`. The
Yahoo provider uses `curl_cffi` (libcurl), which bypasses it. A probe
under pytest received a real Yahoo 404 for `BRK`.

**Resolution:**
- **Guarded entry points:** the guard also patches
  `curl_cffi.requests.Session.request` and `AsyncSession.request`. Every
  curl_cffi call goes through them, including the module-level `get()`
  the provider uses.
- **Same behaviour as sockets:** a non-loopback URL raises
  `curl_cffi`'s `ConnectionError` (an `OSError`, as a dead network gives),
  is listed in the end-of-run "blocked outbound network attempts"
  summary, and honours `MARKETLENS_TEST_ALLOW_NETWORK=1`.
- **Shared logging:** the socket guard's logging was moved into a shared
  helper.

**Verified:** re-running the earlier probe now gives 0 real Yahoo
responses and 3 blocked Yahoo attempts.

**Tests:** in `backend/tests/test_live_resource_isolation.py::TestNetworkGuard`:
- `test_curl_cffi_requests_are_refused_too`
- `test_curl_cffi_async_requests_are_refused_too`

These were not re-run with the guard removed, because that would make a
real request to Yahoo.

### BF-19 — Evidence card lost its 8-item cap; two ChatPanel tests failing

**Status:** ✅ COMPLETE (2026-09-24, batch 2)
**Where:** evidence card in `TypedResponseBlocks`, `frontend/src/components/ChatPanel.tsx:816`.

Found while verifying BF-12: two tests failed on `HEAD` as well as with
the batch 1 changes.

- `ChatPanel (universal) › renders the application-owned answer verification state`
- `ChatPanel (universal) › expands evidence and options-chain tables beyond their default cap, and back (5.7.2)`

**Cause:** commit `af43696` ("collapse Answer verification and Evidence
blocks by default").

- **Answer verification card:** the tests read its body without opening
  it first. A test-only problem.
- **Evidence card:** a real regression. The same `expanded` flag became
  both "card open" and "show every item". An open card therefore always
  listed everything, the 8-item cap was gone, and the "Show all N" button
  could never appear. Only "Show less" remained, and it collapsed the
  whole card.

**Resolution:** the evidence card tracks "show all" under its own key
(`${block.id}:all`). The heading opens the card with the list capped at
8, and "Show all N" / "Show less" toggle the rest. The two tests now open
the card first, and also assert that it starts collapsed.

**Tests:** `ChatPanel.test.tsx` 52/52.

### BF-20 — Two context tests expect an inferred "live" quote status

**Status:** ✅ COMPLETE (2026-09-24, batch 4)
**Where:** `backend/tests/ai/test_context_news_fundamentals.py` (`_fake_scan_result` and two assertions).

Found while checking BF-18's wider impact: these two tests fail on `HEAD`
as well as with batch 4.

- `TestBuildContextNews::test_provider_exception_degrades_to_empty_list`
- `TestBuildContextFundamentals::test_provider_exception_degrades_to_empty_dict`

They failed with `'UNKNOWN' != 'live'`.

**Cause:** commit `669b4e4` ("feat(ai): show market data evidence") made
`build_context` report the provider's own quote status
(`_quote_data_status`, upper-case, "never an inferred one"). The tests'
fake quote is a bare `MagicMock` with no `data_status`, so it reads as
`UNKNOWN`, and they still expected the old inferred `"live"`.

**Resolution:** the fake quote sets `data_status = "LIVE"` and the two
assertions expect `"LIVE"`. Test-only change: the code's behaviour is
intended.

---

## Gaps (not bugs)

All four were fixed on 2026-09-24 in batch 8.

- ~~**Clear without confirmation**~~ (fixed): Clear now asks first
  ("Delete this chat's whole history? This can't be undone.", with
  **Keep history** / **Delete history**), and nothing is deleted until
  **Delete history**. `DELETE /api/ai/chat/sessions` with no filter now
  returns 400; wiping every chat takes an explicit `?all=true`. The
  Playwright helper `clearSessions` (`e2e/tests/chat.spec.ts`) was the
  only bare caller and now passes `?all=true`. Since batch 9 the e2e
  suite runs against its own servers (see Next), so that helper no longer
  touches real history.
- ~~**Blocking query in notebook save**~~ (fixed): the lookup of the
  question to save, the one query in `save_notebook_item` that ran on the
  event loop, now runs through `asyncio.to_thread` like the others.
- ~~**Unchecked model symbol lists**~~ (fixed): the notebook save reads
  `symbols.verified/partial/unavailable` only when each is a list, so a
  string is no longer split into letters and a null no longer fails the
  save. (The server builds these as lists today, in
  `response_blocks.py`; this guards later changes.)
- ~~**Existing lint errors**~~ (fixed): `ruff --fix` sorted the imports in
  `test_chat.py` and `test_chat_calculation.py` and unquoted the
  `BrowserScanFilter` annotation in `chat_router.py` (safe: the module
  uses `from __future__ import annotations`).

**Tests:**
- **API:** in `backend/tests/api/test_chat_router.py`:
  - `test_a_bare_clear_is_refused_instead_of_wiping_everything` and
    `test_clear_all_history_needs_an_explicit_all` (the second replaces
    `test_clear_all_history_no_filter`, which pinned the old wipe).
  - `TestNotebookItemSave` (2 tests, real in-memory database). One checks
    that no query in the save runs on the event loop; the other checks
    that non-list symbol values are skipped.
- **Chat panel:** in `ChatPanel.test.tsx`, the Clear test now confirms
  first, and `Clear keeps the history when the confirmation is declined`
  is new.
- **Mutations:** removing each fix fails its test.
- **Suites:** the affected backend suites pass (`test_chat_router.py`,
  `test_chat.py`, `test_chat_calculation.py`, `test_chat_repository.py`:
  136 tests). `ChatPanel.test.tsx` passes (60) and `tsc` has 0 errors.
  ESLint has no new findings. Neither full suite was run.

### Daily-close freshness (batch 10, found in the live check)

Daily data was judged like intraday data: anything older than 15 minutes
(`_STALE_AFTER_SECONDS`) was stale. So a correct daily-close answer showed
STALE all through the next session, and a daily comparison was withheld
during market hours (`compare_symbols` accepted old daily bars only once
the market had closed).

**Resolution:**
- **Calendar:** `market_calendar.py` gains
  `latest_completed_session_date` (NYSE calendar, holidays included),
  `daily_data_is_current` and `daily_bar_reference_time`.
- **Evidence badge:** `response_blocks.py`'s three copies of the age rule
  are one helper, `_age_status`. Daily (`1d`) evidence dated on or after
  the latest completed session is `recent`; older daily evidence is still
  `stale`; intraday evidence keeps the 15-minute limit.
- **Age:** the registry measures a daily bar stamped at midnight from
  that day's 16:00 ET close, so yesterday's close reads about 18 h, not
  34 h. The tool pill shows "19 h old", not raw seconds (`formatAge`).
- **Price statistics:** requests carry `timeframe="1d"`. The window ends
  at the latest completed session, so a session still trading is left
  out. `source_timestamp` is the last used day's 16:00 ET close.
- **Comparisons:** a daily comparison whose bars include the latest
  completed session is used during market hours too. Older daily bars
  and intraday bars are still withheld.

**Checked live** (2026-09-24, 10:44 ET, in session): "TSLA max drawdown
this year" gave 2026-01-02 to 2026-09-23 (182 closes), −33.95%, Evidence
VERIFIED, and "get_price_statistics · webull · 19 h old".

**Tests:**
- **Freshness rule:** `backend/tests/ai/test_daily_freshness.py` (10
  tests) covers the calendar (including a weekend and Thanksgiving), the
  badge (current, old, intraday), the registry age, the comparison gate
  (in session, current vs old) and the price-statistics scope.
- **Price statistics:** in `test_price_statistics.py`,
  `test_todays_bar_counts_only_once_its_session_has_closed` (noon vs
  17:00) is new. `test_volatility_reports_daily_and_annualized` now
  expects the window to end at the previous close.
- **Chat panel:** `formatAge` in `ChatPanel.test.tsx`.

Removing each change fails its test.

### Reply wording (batch 11, from user feedback)

The server's own replies read like log lines, for example "Verified
compare_symbols comparison by return: AAPL 93.59% (rank 1); MSFT 59.40%
(rank 2). As of the most recent regular-market close; the regular session
is closed." They leaked tool names, used ISO dates and raw provider ids,
and used a "Verified …" prefix that the Answer-verification badge already
covers.

**Bug found while rewriting:** since batch 10, a daily comparison is also
used mid-session, but its note still said "the regular session is
closed". That was false during market hours. The note now depends on the
state: "These figures are as of the last market close." after the close,
and "These use daily closes through the last completed session, so the
session in progress isn't included." mid-session.

**Resolution:** the formatters in `chat.py` now write plain sentences:
- **Replies rewritten:**
  - comparisons ("Ranked by return, AAPL comes first at 93.52%, ahead of
    MSFT at 35.16%.");
  - price statistics ("From Jan 2 to Sep 23, 2026 (182 trading days),
    TSLA's largest drawdown was 33.95%: it dropped from $451.67 on Jan 5
    to $298.32 on Jul 29.");
  - trend ("On the daily chart, NVDA is moving sideways with weak
    strength. Overall, it reads as weak bullish.");
  - market context, indicators, change-since, quotes, support and
    resistance, session stats, collections (news, bars, events),
    fundamentals and options, move evidence, anomalies;
  - portfolio, journal and saved-scan summaries.
- **Shared helpers:** `_nice_date`, `_date_span`, `_chart_name`,
  `_source_name`, `_age_words`, `_count`, `_join_and`, `_as_sentence` and
  `_stale_note`.
- **Verifier rules kept:**
  - Every number is still printed from the evidence.
  - Dates are written out ("Sep 23, 2026").
  - Stale data never uses "current", "today" or "now", and its age is
    given in words.
  - Intraday timeframes stay tokens ("5m").
  - Direction words match the value's sign ("up 1.25%", "down 4.20%").
  - Trend replies keep the data's own labels ("sideways", "weak
    bullish").
- **Unchanged:** calculation replies (already plain sentences), the
  watchlist ranking list, and the trade-plan line, whose "100-102" style
  ranges would become separately checked numbers if reworded.

**Checked live** (2026-09-24, in session): a comparison and a trend
question gave the replies above, both with Answer verification VERIFIED.

**Tests:**
- **Updated:** 23 assertions in 6 test files, plus one Phase 5.8
  evaluation expectation ("session premarket" is now "premarket
  session"). Each checks the same fact in the new wording.
- **New:** the in-session comparison test asserts the reply doesn't
  mention a market close.

## Enhancements

1. ~~**One reply path**~~: done in batch 6 (`_reply_events`, BF-09).
2. ~~**Server-side completion and Cancel**~~: done in batches 6 and 7
   (BF-13).
3. ~~**Market-metric tools**~~: done in batch 5 as `get_price_statistics`
   (BF-04).
4. ~~**Verification-gated streaming**~~: action turns are held (batch 6)
   and other streamed text is labeled as a draft (already in `f24b4cf`),
   BF-08.
5. ~~**Split `chat.py`**~~: done in batch 12. See "Splitting `chat.py`"
   below.

### Splitting `chat.py` (batch 12)

`chat.py` was 5,937 lines. It is now six modules, split by what each
does. Each imports only from the ones listed after it, so there are no
import cycles:

| Module | Lines | Holds |
|---|---|---|
| `chat.py` | ~1,350 | Turn orchestration: context assembly (`_prepare_turn`), the one generation path (`_reply_events`), persistence (`_finish_turn`, `answer_chat_message`, `stream_chat_message`) |
| `chat_routing.py` | ~580 | Replies without the model: `_build_deterministic_chat_reply`, `_run_deterministic_shortcircuit`, `_confirm_pending_action` |
| `chat_actions.py` | ~2,320 | Action handlers (alerts, watchlists, backtest, screen), market tools (`_run_market_tool`), multi-step turns (`_run_turn_actions`, `_finalize_parsed`) |
| `chat_model.py` | ~360 | Model calls: routing, `_complete_and_parse`, `_stream_and_parse`, retries, turn budgets; the only module holding `ai_manager` |
| `chat_intents.py` | ~560 | Regex intents and text parsing (calculator inputs, dates, watchlist names); no I/O |
| `chat_replies.py` | ~1,020 | Plain-sentence replies from verified results |

**How it was done:** the move was mechanical, by a script; apart from the
one pre-edit below, no function body changed:
- **Boundaries:** chosen from a reference graph of the 221 top-level
  names, and checked for import cycles before any code moved.
- **Each item moved with its leading comments** and kept its order; each
  module got `chat.py`'s imports and its own `logger`, and `ruff`
  pruned the unused imports.
- **One pre-edit:** so that a test patching `ai_manager` covers every
  caller, `chat.py` and the backtest handler now read it through
  `_ai_enabled()` and `_ai_setting()` in `chat_model`.

**Tests and harness retargeted:** a patch replaces a name where it is
looked up, so patch strings now name the module that uses each name:
- `ai_manager` goes to `chat_model` (61 patches).
- `default_registry`, `analyze_symbol`, `_run_action`,
  `_kickoff_backfill`, `_ACTION_HANDLERS` and `_is_regular_market_closed`
  go to `chat_actions`.
- `now_ny` for the routing test goes to `chat_routing`; it still
  resolves on `chat`, so this one was changed by hand.
- Imports of moved names were rewritten to their new modules.
- The Phase 5.8 evaluation harness (`evaluations/chat_runner.py`) got the
  same changes.
- Four comments that named `backend.ai.chat.<function>` now name the new
  module.

**Checks:**
- **Suites:** `backend/tests/ai` and `backend/tests/api`, 1,551 passed
  (the only test directories that use these modules).
- **Lint:** `ruff` is clean on all six modules.
- **Dev server:** it reloaded cleanly.
- **Browser:** the full e2e suite passed, 31 of 31, on the isolated
  servers.
- **Network attempts:** the guard's report showed attempts in two tests;
  see the time-dependent network item in "Next". They are not from the
  split.

## Verification

### Batch 12 (2026-09-24): `chat.py` split

| Suite | Result |
|---|---|
| `backend/tests/ai`, `backend/tests/api` | 1,551 passed, 30 subtests passed |
| `backend/tests/ai`, `backend/tests/api`, `backend/tests/engines` (before the docstring edit) | 1,630 passed |
| `ruff` on the six Chat modules and every rewritten file | clean |
| Full e2e suite on the isolated servers | 31 passed in 2.4 min; live chat unchanged |
| Dev server | reloaded with the new modules, health 200 |

**Full backend suite (run before the batch 12 commit):** 3,380 passed, 49
subtests passed, 0 failed, in 45 s; no blocked outbound network attempts.

### Batch 11 (2026-09-24): natural reply wording

| Suite | Result |
|---|---|
| `backend/tests/ai`, `backend/tests/api`, `backend/tests/engines` | 1,630 passed, 30 subtests passed |
| `ruff` on the changed files | clean |
| Live check in the running app | comparison and trend replies in plain sentences, both VERIFIED |

Neither full suite was run. The e2e checks use broad patterns
(`/AAPL|quote/i`) and don't depend on the old wording.

### Batch 10 (2026-09-24): daily-close freshness

| Suite | Result |
|---|---|
| `backend/tests/ai`, `backend/tests/engines`, `backend/tests/api` | 1,630 passed, 30 subtests passed |
| `backend/tests/ai/test_daily_freshness.py` (new) | 10 passed |
| `backend/tests/ai/test_price_statistics.py` | 39 passed (2 new cases; 1 updated) |
| `frontend/src/components/ChatPanel.test.tsx` | 62 passed (1 new) |
| `tsc --noEmit`; `ruff`; `eslint` on changed files | clean |
| Live check in the running app | the drawdown answer ends at 09-23 (182 closes); Evidence VERIFIED; "19 h old" |

**Mutation check:** removing each change fails its test:
- the badge's daily rule;
- measuring age from the close;
- the in-session comparison rule;
- the price-statistics scope;
- the window cap (fails 2).

Neither full suite was run.

### Batch 9 (2026-09-24): follow-ups, live checks, isolated e2e

| Suite | Result |
|---|---|
| `backend/tests/ai` | 1,027 passed, before the BF-16 test additions |
| `test_chat_calculation.py`, `test_chat_actions.py` | pass, including the new suffix and `needs_input` tests |
| `backend/tests/market_data/test_yfinance_provider.py` | 25 passed (2 new; 1 updated) |
| `frontend/src/components/ChatPanel.test.tsx` | 61 passed (1 new) |
| `tsc --noEmit` | 0 errors |
| e2e group A on the isolated servers | 4 passed; live chat unchanged |
| Full e2e suite on the isolated servers (run after batch 11) | 31 passed in 2.1 min; live chat unchanged |

**Mutation check:** removing each fix fails its tests:
- suffix scaling fails 1;
- the Yahoo translation fails 2;
- the `needs_input` rule fails 3.

Neither full suite was run.

### Batch 8 (2026-09-24): the four gaps

| Suite | Result |
|---|---|
| `test_chat_router.py`, `test_chat.py`, `test_chat_calculation.py`, `test_chat_repository.py` | 136 passed (4 new tests; 1 replaced) |
| `frontend/src/components/ChatPanel.test.tsx` | 60 passed (1 new; the Clear test now confirms first) |
| `tsc --noEmit` | 0 errors |
| `ruff check` on the changed backend files | 0 errors (the 3 older ones are fixed) |
| `eslint` on the changed frontend files | No new findings (`ChatPanel.test.tsx` keeps its 24 older ones, as on `HEAD`) |

**Mutation check:** removing each fix fails its test:
- the bare-clear 400;
- running the question lookup in a worker thread;
- the list check;
- the Clear confirmation.

Neither full suite was run for this batch.

### Batch 7 (2026-09-24)

| Suite | Result |
|---|---|
| `frontend/src/services/api.test.ts` | 12 passed (5 new) |
| `frontend/src/components/ChatPanel.test.tsx` | 59 passed (7 new) |
| Whole frontend suite (`react-scripts test`) | 43 suites, 240 tests passed |
| `tsc --noEmit` | 0 errors |
| `eslint` (the project's `react-app` config) on the changed files | No new findings. `ChatPanel.test.tsx` has 24 older testing-library findings, the same as on `HEAD`. |

No backend files changed in this batch.

### Batch 6 (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/ai/test_chat_reply_paths.py` (new) | 13 passed; 10 of them failed on the old code |
| `backend/tests/ai`, `backend/tests/api`, `backend/tests/repositories`, `backend/tests/integration` | 1,703 passed, 30 subtests passed |
| `ruff check` on the changed files | 1 error (`chat_router.py:186` UP037), also on `HEAD` |

**Mutation check:** each backend change was removed in turn, and exactly
its test fails:
- the intent hold;
- the early-action check;
- saving the user row last;
- the drain's `continue`.

**Full backend suite (run before the batch 6 commit):**
- 3,361 passed, 49 subtests passed, 0 failed, in 44 s. That is 13 more
  than batch 5's run: the new tests.
- No blocked outbound network attempts.
- The same unclosed-transport `ResourceWarning` as batch 5, on the same
  test (`test_ai_manager.py::TestProviderStreaming::test_openai_stream_raises_unavailable_on_429`).

### Batch 5 (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/ai/test_price_statistics.py` | 37 passed |
| `backend/tests/ai`, `test_chat_router.py`, `test_chat_repository.py` | 1,086 passed, 30 subtests passed |
| `ruff check` on every changed file | All checks passed (one import-order fix applied in `chat.py`) |

**Mutation check:**
- Removing the price-metric route fails 3 tests.
- Restoring the old `_REUSE_MEMORY_HINT` fails 1.

**Full backend suite (run before the batch 5 commit):**
- 3,348 passed, 49 subtests passed, 0 failed, in 44 s. That is 37 more
  than batch 4's run: the new tests.
- No blocked outbound network attempts.
- The same unclosed-transport `ResourceWarning` appeared, this time
  attributed to `test_ai_manager.py::TestProviderStreaming::test_openai_stream_raises_unavailable_on_429`
  rather than batch 4's `test_chat.py` test. Since it moves between
  tests, it is likely released during cleanup from an earlier test
  (unconfirmed).

### Batch 4 (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/market_data`, `backend/tests/ai`, `backend/tests/symbols`, `test_live_resource_isolation.py`, `test_chat_router.py`, `test_chat_repository.py` | 1,535 passed, 30 subtests passed |
| `ruff check` on every changed file | All checks passed |

These suites were chosen because the conftest change applies to every
test and they are the ones that use curl_cffi or the Chat code.

**Mutation check:**
- Each fix for BF-14, BF-15 and BF-16 was removed in turn, and its tests
  fail without it.
- BF-18 was not re-run with the guard removed (a real Yahoo request).

**Full backend suite (run after the batch 4 commit, `43c5e17`):**
- 3,311 passed, 49 subtests passed, 0 failed, in 46 s.
- No blocked outbound network attempts were reported, so no test was
  still depending on a real Yahoo call.
- One `ResourceWarning` (unclosed asyncio transport) in
  `test_chat.py::TestTurnIntent::test_news_question_pulls_news`.

### Batch 3 (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/migrations/` (all migration tests, including the 2 new ones), `test_chat_repository.py`, and all Chat-related backend tests | 485 passed, 13 subtests passed |
| Migration on a copy of the live DB (seeded rows, upgrade → downgrade → upgrade) | Rows kept, orphan feedback removed, next id above every reference |
| Live DB after the reload | Revision `20260930_chat_id_autoincrement`, both tables `AUTOINCREMENT`, server healthy |
| `ruff check` on the new migration | I001 import order, the same as the existing migrations (for example `20260929_chat_notebooks_and_fixtures.py`) |

**Mutation check:** with `sqlite_autoincrement` removed from the models,
`test_ids_are_not_reused_after_a_clear` fails.

The full backend suite was not run.

### Batch 2 (2026-09-24)

| Suite | Result |
|---|---|
| All Chat-related backend tests (`backend/tests/ai/test_chat*.py`, `test_phase_5_8*.py`, `test_answer_verifier.py`, `test_response_blocks.py`, `backend/tests/api/test_chat_router.py`) | 451 passed, 13 subtests passed |
| `frontend/src/components/ChatPanel.test.tsx` | 52 passed (BF-19 fixed the 2 failures) |
| `tsc --noEmit` on the frontend | No errors in ChatPanel |
| `ruff check` on the changed backend files | 1 error (`test_chat.py:15` I001), also present on `HEAD` |

**Mutation check:** each new backend test was re-run with its fix removed
and fails without it.
- No rollback: the flush test fails with `PendingRollbackError`.
- No deterministic confirmation: both BF-06 "yes" tests fail.
- No target resolution: 5 of the 6 BF-07 tests fail.
- No blocking generation guard: the blocking generation test fails.

The full backend suite was not run.

### Batch 1 (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/ai/test_chat_calculation.py`, `test_chat_actions.py`, `backend/tests/api/test_chat_router.py` | 199 passed, 13 subtests passed |
| All Chat-related backend tests (`backend/tests/ai/test_chat*.py`, `test_phase_5_8*.py`, `test_answer_verifier.py`, `test_response_blocks.py`) | 394 passed |
| `frontend/src/components/ChatPanel.test.tsx` | 50 passed, 2 failed (BF-19; both also fail on `HEAD`) |
| `tsc --noEmit` on the frontend | No errors in ChatPanel |
| `ruff check` on the changed backend files | 2 errors, both also present on `HEAD` (see Gaps) |

The full backend suite was not run.

## Fix log

- **Batch 1:** commit `b5afc49`, `fix(chat): harden confirmations, calculator fallbacks, and turn arguments`, on `development`.
- **Batch 2:** commit `4381ae1`, `fix(chat): confirm safely with AI off and keep failed turns persistable`, on `development`.
- **Batch 3:** commit `f251177`, `fix(chat): never reuse chat message and session ids`, on `development` (the migration was applied to the live DB before the commit).
- **Batch 4:** commit `43c5e17`, `fix(chat): resolve class tickers and watchlist names, stop chains on questions`, on `development`.
- **Batch 5:** commit `b73f8d3`, `feat(chat): answer return, volatility, drawdown and correlation questions`, on `development`.
- **Batch 6:** commit `7aab462`, `fix(chat): one reply path for both transports; finish turns after disconnect`, on `development`.
- **Batch 7:** commit `2728f43`, `feat(chat): cancel and time out streamed replies without resending`, on `development`.
- **Docs:** commit `12420e5`, `docs(v5): correct tool and action counts; record housekeeping`, and commit `52af7dc`, `docs(v5): update BF-12 follow-up for alert-chat polling`, on `development`.
- **Batch 8 (gaps):** commit `58abaf1`, `fix(chat): confirm Clear, refuse bare history wipes, keep notebook save off the event loop`, on `development`.
- **Batch 9 (follow-ups):** commit `8ce8fa9`, `fix(chat): read $10k account sizes, ask Yahoo for BRK-B, label question steps, isolate e2e`, on `development`.
- **Batch 10 (daily-close freshness):** commit `be6ba00`, `fix(chat): judge daily data by session date, not a 15-minute age limit`, on `development`.
- **Batch 11 (reply wording):** commit `8f2d5c5`, `fix(chat): write server replies as plain sentences`, on `development`.
- **Batch 12 (`chat.py` split):** commit `refactor(chat): split chat.py into six modules by role`, on `development`.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | BF-01 | ✅ COMPLETE | batch 1 | `backend/ai/chat.py` | `test_chat_actions.py::TestConfirmationAffirmation` | `_AFFIRM_INTENT` matches only a whole-message affirmation. |
| 2026-09-24 | BF-02 | ✅ COMPLETE | batch 1 | `backend/ai/chat.py` | `test_chat_calculation.py` (3 tests) | Labelled-field parsing; new `_RISK_PERCENT_RE`, `_ACCOUNT_BEFORE_RE`. |
| 2026-09-24 | BF-03 | ✅ COMPLETE (finished in batch 5) | batch 1 | `backend/ai/chat.py` | `test_chat_calculation.py` (2 tests) | New `_CALC_DATE_RE`; the fallback steps aside when a date is present. |
| 2026-09-24 | BF-05 | ✅ COMPLETE | batch 1 | `backend/api/ai/chat_router.py`, `backend/tests/api/test_chat_router.py` | `test_scope_without_mode_is_not_passed_as_chart_state` + 6 updated assertions | Keyword arguments via `_turn_kwargs`. |
| 2026-09-24 | BF-12 | ✅ COMPLETE | batch 1 | `frontend/src/components/ChatPanel.tsx`, `ChatPanel.test.tsx` | `mergePolledMessages (BF-12)` (3 tests) | Poll swaps the server user row into the optimistic message. |
| 2026-09-24 | BF-06 | ✅ COMPLETE | batch 2 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_actions.py` | `TestEndToEnd` (3 tests) | `_confirm_pending_action` runs before routing, the model and the AI-off fallback. |
| 2026-09-24 | BF-07 | ✅ COMPLETE | batch 2 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_actions.py` | `TestDestructiveTargetResolution` (6 tests) | `_destructive_target_problem` resolves the target first; the alert is named; the watchlist id is pinned. |
| 2026-09-24 | BF-11 | ✅ COMPLETE | batch 2 | `backend/ai/chat.py`, `backend/tests/ai/test_chat.py`, `backend/tests/ai/test_chat_actions.py` | `TestTurnFailureStillPersistsOneReply` (3 tests), flush-rollback test | `_rollback_quietly`; shared `_finish_turn`; blocking generation guard. |
| 2026-09-24 | BF-19 | ✅ COMPLETE | batch 2 | `frontend/src/components/ChatPanel.tsx`, `ChatPanel.test.tsx` | `ChatPanel.test.tsx` 52/52 | Evidence "show all" has its own state; tests open the collapsed cards first. |
| 2026-09-24 | BF-10 | ✅ COMPLETE | batch 3 | `alembic/versions/20260930_chat_id_autoincrement.py`, `backend/models/chat.py`, `backend/repositories/chat_repository.py`, 2 test files | migration tests (2), repository tests (2) | AUTOINCREMENT on sessions/messages; orphan feedback removed; sequence seeded above every reference; feedback deleted on Clear. |
| 2026-09-24 | BF-14 | ✅ COMPLETE | batch 4 | `backend/ai/chat_symbols.py`, `backend/tests/ai/test_chat_symbols.py` | 3 tests | Share-class suffix kept and written as `BRK.B`. |
| 2026-09-24 | BF-15 | ✅ COMPLETE | batch 4 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_actions.py` | 7 tests | Case-insensitive watchlist names; no list created from an unknown name; "new watchlist called Y" routes to create. |
| 2026-09-24 | BF-16 | ✅ COMPLETE | batch 4 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_actions.py` | 2 new, 2 updated | Clarifications are ungrounded; chains stop after a failed or questioning step. |
| 2026-09-24 | BF-17 | ✅ COMPLETE | batch 4 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_actions.py` | 1 test | `_BARE_NUM` replaces the `_NUM[4:]` slice. |
| 2026-09-24 | BF-18 | ✅ COMPLETE | batch 4 | `backend/tests/conftest.py`, `backend/tests/test_live_resource_isolation.py` | 2 tests | curl_cffi `Session.request` / `AsyncSession.request` refused under pytest. |
| 2026-09-24 | BF-20 | ✅ COMPLETE | batch 4 | `backend/tests/ai/test_context_news_fundamentals.py` | 2 tests fixed | Fake quote carries `data_status="LIVE"`. |
| 2026-09-24 | BF-04 | ✅ COMPLETE | batch 5 | `backend/ai/market_tools.py`, `backend/ai/price_metric_intent.py` (new), `backend/ai/calculator.py`, `backend/ai/tool_registry.py`, `backend/ai/chat.py`, `backend/ai/prompt.py`, `backend/tests/ai/test_price_statistics.py` (new), `backend/tests/ai/test_tool_registry.py` | 37 tests | New `get_price_statistics` tool and parser; `return_correlation`; explicit reuse wording. |
| 2026-09-24 | BF-03 | ✅ COMPLETE | batch 5 | (via BF-04) | `test_chat_routes_metric_questions_to_the_tool` | Date-range return questions now get a verified answer. |
| 2026-09-24 | BF-09 | ✅ COMPLETE | batch 6 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_reply_paths.py` (new), `backend/tests/ai/test_chat_intents.py` | 7 tests | One generator, `_reply_events`, for both transports. |
| 2026-09-24 | BF-08 | ✅ COMPLETE (finished in batch 7) | batch 6 | `backend/ai/chat.py`, `backend/tests/ai/test_chat_reply_paths.py` | 3 tests | No streamed text for action-like requests or once an action appears; frontend draft marking remains. |
| 2026-09-24 | BF-13 | ✅ COMPLETE (finished in batch 7) | batch 6 | `backend/ai/chat.py`, `backend/api/ai/chat_router.py`, `backend/tests/ai/test_chat_reply_paths.py` | 3 tests | Turns finish after a disconnect; the user row is saved last. Frontend timeout/Cancel remains. |
| 2026-09-24 | BF-13 | ✅ COMPLETE | batch 7 | `frontend/src/services/api.ts`, `frontend/src/components/ChatPanel.tsx`, `frontend/src/services/api.test.ts`, `frontend/src/components/ChatPanel.test.tsx` | 12 tests | Idle timeout; Cancel; cancel on session switch/unmount; no resend; the saved reply is fetched instead. |
| 2026-09-24 | BF-08 | ✅ COMPLETE | batch 7 | (no code; see entry) | existing draft-label test | The draft label already existed (`f24b4cf`); with batch 6's gating nothing remained. |
| 2026-09-24 | Gaps | ✅ FIXED | batch 8 | `backend/api/ai/chat_router.py`, `frontend/src/components/ChatPanel.tsx`, `e2e/tests/chat.spec.ts`, two test files (lint), plus tests | 5 new, 2 updated | Clear confirms first; a bare `DELETE /sessions` is refused (`?all=true` wipes all); notebook save fully off the event loop and type-checks symbol lists; 3 `ruff` errors fixed. |
| 2026-09-24 | Follow-ups | ✅ FIXED | batch 9 | `backend/ai/chat.py`, `backend/market_data/providers/yfinance_provider.py`, `frontend/src/components/ChatPanel.tsx`, `frontend/src/services/api.ts`, `frontend/src/styles/App.css`, `e2e/playwright.config.ts`, `e2e/tests/chat.spec.ts`, plus tests | 6 new, 3 updated | `$10k` account sizes; `BRK.B` at Yahoo; `needs_input` step status; isolated e2e servers; live checks in the running app. |
| 2026-09-24 | Daily freshness | ✅ FIXED | batch 10 | `backend/engines/market_calendar.py`, `backend/ai/response_blocks.py`, `backend/ai/tool_registry.py`, `backend/ai/market_tools.py`, `backend/ai/chat.py`, `frontend/src/components/ChatPanel.tsx`, plus tests | 13 new, 1 updated | Daily evidence judged by session date; age from the close; price statistics end at the last completed session; daily comparisons usable in session; ages shown as "19 h". |
| 2026-09-24 | Reply wording | ✅ FIXED | batch 11 | `backend/ai/chat.py`, `backend/ai/evaluations/phase_5_8_chat_cases.json`, 6 test files | 23 assertions updated, 1 added | Server replies in plain sentences; mid-session comparisons no longer claim the market is closed. |
| 2026-09-24 | Split `chat.py` | ✅ DONE | batch 12 | `backend/ai/chat.py` plus 5 new `chat_*.py` modules, `evaluations/chat_runner.py`, 12 test files, 3 comment-only files | 0 behaviour changes | 5,937-line module split into six by role; no cycles; tests patch where names are used. |
