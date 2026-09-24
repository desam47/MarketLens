# Version 5 Chat Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (first fix batch: BF-01, BF-02, BF-03, BF-05, BF-12)
**Status:** In progress. Batch 1 (BF-01, BF-02, BF-03, BF-05, BF-12) is committed.
**Scorecard:** 4 ✅ COMPLETE, 1 ⚠️ PARTIAL, 14 ❌ NOT STARTED, 0 🟡 DEFERRED.
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

Line numbers refer to the working tree after the first fix batch. That
batch added about 40 lines near the top of `chat.py`, so references in
older notes are off by that much.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| BF-01 | Critical | Backend | Affirmation prefix confirms destructive actions | Verified | ✅ COMPLETE |
| BF-02 | High | Backend | Position-size fallback maps numbers by order | Verified | ✅ COMPLETE |
| BF-03 | High | Backend | Date numbers become calculator inputs | Verified | ⚠️ PARTIAL |
| BF-04 | Medium | Backend | Market-metric questions dead-end in the calculator | Verified | ❌ NOT STARTED |
| BF-05 | Medium | API | Positional argument misbinding in Chat router | Verified | ✅ COMPLETE |
| BF-06 | Medium | Backend | Destructive actions cannot be confirmed with AI off | Code-read | ❌ NOT STARTED |
| BF-07 | Medium | Backend | Delete-alert confirmation does not name the alert | Code-read | ❌ NOT STARTED |
| BF-08 | Medium | Backend | Stream shows unverified model text before verification | Code-read | ❌ NOT STARTED |
| BF-09 | Medium | Backend | Streaming and blocking reply paths have drifted | Code-read | ❌ NOT STARTED |
| BF-10 | Medium | Data | Message ids reused after Clear; feedback reattaches | Verified (live DB) | ❌ NOT STARTED |
| BF-11 | Medium | Backend | Failed action leaves DB session unusable | Code-read | ❌ NOT STARTED |
| BF-12 | Medium | Frontend | Nudge poll duplicates the user's message | Code-read | ✅ COMPLETE |
| BF-13 | Medium | Full stack | Stream timeout, disconnect, and resend gaps | Code-read | ❌ NOT STARTED |
| BF-14 | Low | Backend | Dotted tickers (BRK.B) rejected | Verified | ❌ NOT STARTED |
| BF-15 | Low | Backend | Add-to-watchlist silently creates a mistyped watchlist | Code-read | ❌ NOT STARTED |
| BF-16 | Low | Backend | Clarification questions recorded as completed steps | Code-read | ❌ NOT STARTED |
| BF-17 | Low | Backend | `_SHARES_RE` slice leaves a literal `s*` | Code-read | ❌ NOT STARTED |
| BF-18 | Low | Tests | Network guard does not block curl_cffi (Yahoo) | Verified | ❌ NOT STARTED |
| BF-19 | Low | Tests | Two ChatPanel tests fail on committed code | Verified | ❌ NOT STARTED |

**Next suggested order:**

1. BF-11 (a one-line rollback plus a guard).
2. BF-06 (reuses the stricter BF-01 affirmation).
3. BF-07.
4. BF-10 (needs a migration; validate it on a DB copy first).
5. BF-04.
6. The streaming group: BF-08, BF-09, BF-13.

---

## Critical

### BF-01 — Affirmation prefix confirms destructive actions

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `backend/ai/chat.py:557` (`_AFFIRM_INTENT`), used at `chat.py:3229` and `chat.py:3252`, and by `_fallback_confirmation`.

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
- A `k` suffix ("$10k account") is not parsed yet; that input is treated
  as missing.
- Inputs listed without labels ("entry, stop, target: 100, 95, 110") now
  get a clarifying question instead of a calculation.

### BF-03 — Date numbers become calculator inputs

**Status:** ⚠️ PARTIAL (2026-09-24, batch 1)
**Where:** `_fallback_calculation`, `chat.py:683`; the new `_CALC_DATE_RE` at `chat.py:608`.

**Reproduced:** "What was NVDA's return from Jan 5 to Jan 20?" produced
`percentage_change(old=5, new=20)`, i.e. +300%.

**Why ⚠️ PARTIAL:** the wrong answer is gone, but the date-range question
still gets no real answer. The remaining work is the price-history return
tool from the original fix (see Follow-ups, BF-04 and Enhancement 3).

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
- **No date-range answer yet:** "return from Jan 5 to Jan 20" no longer
  gets a wrong number, but it also does not get a real answer. That needs
  the date-range return tool from BF-04 and Enhancement 3.
- **Bare years:** these ("2024 to 2025") are not treated as dates,
  because `from 2000 to 2500` can be real prices. The ticker case is
  tracked under BF-04.
- **"may" false positive:** "may" is matched as a month when followed by
  a number ("may 5"). This is unlikely in calculator wording but possible.

---

## Medium

### BF-04 — Market-metric questions dead-end in the calculator

**Status:** ❌ NOT STARTED
**Where:** `chat.py:2477-2499`, `_CALCULATION_HINT` at `chat.py:570`, `_REUSE_MEMORY_HINT` at `chat.py:575`.

**Reproduced:**

- "What is TSLA's max drawdown this year?", "What's AAPL volatility over
  30 days?" and "What's the correlation between AAPL and MSFT?" all reply
  "What values should I use for that calculation?"
- "what's the return on it" silently re-runs the remembered
  `position_risk` calculation with stale inputs.
- "percent change for AAPL from 2024 to 2025" routes to today's 1d
  `change_percent`.

**Fix:** when a ticker is resolved, send drawdown, volatility, correlation
and return questions to market tools that compute from bars. Remove `it`
from the reuse hint (or require an explicit "same/previous calculation").
Reject or ask about explicit date ranges instead of answering with today's
change.

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

**Status:** ❌ NOT STARTED
**Where:** the confirmation replay lives only in `_finalize_parsed`
(`chat.py:3226-3238`), which runs only after a model reply. With AI off,
"yes" goes to `_deterministic_context_reply` (`chat.py:2953`, and
`chat.py:3681` in streaming), and `_expire_carried_confirmation` drops the
pending action.

**Fix:** check `pending_confirmation` plus the strict affirmation from
BF-01 deterministically, before the model or the AI-off fallback.

### BF-07 — Delete-alert confirmation does not name the alert

**Status:** ❌ NOT STARTED
**Where:** `_confirm_prompt`, `chat.py:3983` (the `delete_alert` branch is at `chat.py:3996`).

The prompt is "Delete that alert? Say yes to confirm." The target id is
model-chosen, so the trader confirms without seeing what will be deleted.

**Fix:** look up the alert and include its name, symbol, condition and
threshold. If the id does not exist, refuse before asking.
The pending `delete_watchlist` has the same weakness: it stores the name,
not the id resolved at prompt time. Store the resolved id so the target
cannot change between prompt and "yes".

### BF-08 — Stream shows unverified model text before verification

**Status:** ❌ NOT STARTED
**Where:** `_generate_reply_streaming`, `chat.py:3730-3734`.

Deltas are the model's raw `reply` field, streamed before `verify_answer`
and before any action runs. The note at `chat.py:544-551` records that the
model sometimes writes a fake "Done — deleted…"; that text is visible until
the `final` frame overwrites it.

**Fix:** do not stream deltas when the parsed action is not `none`. Render
streamed text as a clearly marked draft until `final` arrives. An
alternative is to buffer until verification passes.

### BF-09 — Streaming and blocking reply paths have drifted

**Status:** ❌ NOT STARTED
**Where:** `_generate_reply` (`chat.py:2856`) vs `_generate_reply_streaming` (`chat.py:3585`).

- **No-data watchlist exemption:** the legacy "no data" degrade in
  streaming (`chat.py:3613`) lacks the watchlist-intent exemption the
  blocking path has (`chat.py:2893`). In a single-ticker session for a
  ticker with no data, streaming refuses "add it to my watchlist".
- **Parse failures:** streaming returns `extractor.text` (the raw
  unparsed model reply) as the answer (`chat.py:3811`). Blocking returns
  "I couldn't process that — could you rephrase?"
- **Time budget:** blocking passes `started_at` to the deterministic
  short-circuit; streaming does not, so the turn time budget differs.

**Fix:** have a single generator implementation, with the blocking path
as a thin wrapper that drains it.

### BF-10 — Message ids reused after Clear; feedback reattaches

**Status:** ❌ NOT STARTED
**Where:** `ChatRepository.delete_sessions` (`backend/repositories/chat_repository.py`).

`delete_sessions` removes messages and sessions but not `chat_feedback`,
`chat_regression_fixtures` or notebook items that reference message ids.
The chat tables have no `AUTOINCREMENT`, so SQLite reuses freed ids. The
live DB shows `count(chat_messages)=2, max(id)=2` after a clear.

**Impact:** old ratings can appear on new messages. Promoting a new
message to a fixture can hit `UNIQUE(message_id)` and fail with a 500.

**Fix:**

- Delete (or detach) dependent feedback rows in `delete_sessions`.
- Add `sqlite_autoincrement=True` to the chat tables via a migration.
  Validate it on a DB copy first; see the `--reload` note in project memory.
- Decide whether regression fixtures survive a clear. They copy prompt and
  response, so they can drop the FK.

### BF-11 — Failed action leaves DB session unusable

**Status:** ❌ NOT STARTED
**Where:** `_run_action`, `chat.py:5408`.

The exception is caught but `db.rollback()` is never called. If a flush
failed (e.g. `IntegrityError`), the next `repo.add_message` on the same
session raises `PendingRollbackError`. In the stream path the finalization
fallback uses the same session, so no assistant row is saved.

`answer_chat_message` (`chat.py:1982`) also has no guard after the user
message is saved, so any failure becomes a 500.

**Fix:** roll back in the `except` branch of `_run_action`. Give
`answer_chat_message` the same "always persist one assistant row"
finalization as `stream_chat_message`.

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
- Alert-scoped sessions never poll, so they were never affected.

### BF-13 — Stream timeout, disconnect, and resend gaps

**Status:** ❌ NOT STARTED
**Where:** `frontend/src/services/api.ts` (`streamChatMessage`), `ChatPanel.tsx:388`, `chat_router.py:869-871`, `chat.py:1185`.

- **No timeout:** the stream has no timeout or `AbortController`. A hung
  stream leaves `sending=true` and the input locked; unmounting does not
  cancel.
- **Disconnect:** on client disconnect the drain loop breaks and the
  generator is abandoned. The user message is saved with no assistant
  reply, and actions may be half-done.
- **Resend:** an exception in `_prepare_turn` after
  `repo.add_message(..., "user", ...)` but before `meta` reports
  `started=false`. The client then falls back to the blocking endpoint and
  saves the user message twice.

**Fix:**

- Add a timeout, an `AbortController` and a Cancel button in the UI.
- Run the turn to completion server-side regardless of the client, and
  persist the reply.
- Mark the turn as started as soon as the user row is saved (or save it
  after context assembly).

---

## Low

### BF-14 — Dotted tickers rejected

**Status:** ❌ NOT STARTED

"price of BRK.B" resolves to rejected symbol `BRK` (verified). Browser
position models already allow dots (`chat_router.py:154`). Fix the ticker
extraction in `backend/ai/chat_symbols.py` to keep class suffixes.

### BF-15 — Add-to-watchlist silently creates a mistyped watchlist

**Status:** ❌ NOT STARTED

`_add_to_watchlist` (`chat.py:4147`; the create call is at `chat.py:4159`)
creates a new watchlist when the named one is not found. A typo creates a
stray list. Fix: ask ("No watchlist called 'Tehc' — create it?") or
fuzzy-match existing names.

Symbols rejected as unresolvable are also added (and backfilled) through
the deterministic CRUD path (`chat.py:2820-2830`). Consider requiring a
known ticker, or asking for confirmation, for those.

### BF-16 — Clarification questions recorded as completed steps

**Status:** ❌ NOT STARTED

The ambiguity replies in `_add_to_watchlist`, `_remove_from_watchlist` and
`_delete_watchlist` return `grounded=True`. `_run_turn_actions` logs them as
`completed`, and a multi-step chain continues. Fix: return `False` (or a
distinct "needs_input" status).

### BF-17 — `_SHARES_RE` slice leaves a literal `s*`

**Status:** ❌ NOT STARTED

`_NUM[4:]` (`chat.py:585`) strips `\$?\` but leaves `s*`, so the pattern
contains "zero or more literal `s`". Harmless today. Define a separate
unprefixed number pattern instead of slicing.

### BF-18 — Network guard does not block curl_cffi (Yahoo)

**Status:** ❌ NOT STARTED

`backend/tests/conftest.py` patches `socket.socket.connect`. The Yahoo
provider uses `curl_cffi` (libcurl), which bypasses it. A probe under
pytest received a real Yahoo 404 for `BRK`. Fix: patch
`curl_cffi.requests` in the guard, or stub the Yahoo provider in tests.

### BF-19 — Two ChatPanel tests fail on committed code

**Status:** ❌ NOT STARTED

Found while verifying BF-12. These tests fail both with and without the
BF-12 change. They were run against the committed `ChatPanel.tsx` and
`ChatPanel.test.tsx` from `HEAD` (`165d882`).

- `ChatPanel (universal) › renders the application-owned answer verification state`
- `ChatPanel (universal) › expands evidence and options-chain tables beyond their default cap, and back (5.7.2)`
  (fails at `expect(evidenceRegion.querySelectorAll('li')).toHaveLength(8)`)

**Likely cause (not confirmed):** commit `af43696` ("collapse Answer
verification and Evidence blocks by default"). If so, the tests need to
expand the block before counting rows.

---

## Gaps (not bugs)

- **Clear without confirmation:** the Clear button deletes the universal
  chat history with no confirmation. `DELETE /api/ai/chat/sessions` with
  no filter wipes every session.
- **Blocking query in notebook save:** `save_notebook_item`
  (`chat_router.py:660`) runs a synchronous DB query on the event loop.
- **Unchecked model symbol lists:** the notebook save walks
  `symbols.verified/partial/unavailable` without checking they are lists,
  so a string would be split into characters.
- **Existing lint errors:** two `ruff` errors are also present in the
  committed files. They are auto-fixable and unrelated to these fixes.
  - `backend/api/ai/chat_router.py:186` UP037: quoted forward reference
    `"BrowserScanFilter"`.
  - `backend/tests/ai/test_chat_calculation.py:1` I001: import block not
    sorted.

## Enhancements

1. **One reply path:** a single turn generator for streaming and blocking
   (removes BF-09-class drift).
2. **Server-side completion:** turns finish on the server even if the
   client disconnects, plus a Cancel button in the UI.
3. **Market-metric tools:** period drawdown, volatility, correlation and
   date-range return as tools. These give BF-03 and BF-04 real answers
   instead of a clarifying question.
4. **Verification-gated streaming:** hold or mark streamed text until
   verification passes (BF-08).
5. **Split `chat.py`:** the module is ~5,450 lines. Split it into intent
   routing, actions, turn orchestration and formatting.

## Verification (first fix batch, 2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/ai/test_chat_calculation.py`, `test_chat_actions.py`, `backend/tests/api/test_chat_router.py` | 199 passed, 13 subtests passed |
| All Chat-related backend tests (`backend/tests/ai/test_chat*.py`, `test_phase_5_8*.py`, `test_answer_verifier.py`, `test_response_blocks.py`) | 394 passed |
| `frontend/src/components/ChatPanel.test.tsx` | 50 passed, 2 failed (BF-19; both also fail on `HEAD`) |
| `tsc --noEmit` on the frontend | No errors in ChatPanel |
| `ruff check` on the changed backend files | 2 errors, both also present on `HEAD` (see Gaps) |

The full backend suite was not run.

## Fix log

Batch 1 is the commit `fix(chat): harden confirmations, calculator fallbacks, and turn arguments` on `development` (find it with `git log --grep "harden confirmations"`).

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | BF-01 | ✅ COMPLETE | batch 1 | `backend/ai/chat.py` | `test_chat_actions.py::TestConfirmationAffirmation` | `_AFFIRM_INTENT` matches only a whole-message affirmation. |
| 2026-09-24 | BF-02 | ✅ COMPLETE | batch 1 | `backend/ai/chat.py` | `test_chat_calculation.py` (3 tests) | Labelled-field parsing; new `_RISK_PERCENT_RE`, `_ACCOUNT_BEFORE_RE`. |
| 2026-09-24 | BF-03 | ⚠️ PARTIAL | batch 1 | `backend/ai/chat.py` | `test_chat_calculation.py` (2 tests) | New `_CALC_DATE_RE`; the fallback steps aside when a date is present. |
| 2026-09-24 | BF-05 | ✅ COMPLETE | batch 1 | `backend/api/ai/chat_router.py`, `backend/tests/api/test_chat_router.py` | `test_scope_without_mode_is_not_passed_as_chart_state` + 6 updated assertions | Keyword arguments via `_turn_kwargs`. |
| 2026-09-24 | BF-12 | ✅ COMPLETE | batch 1 | `frontend/src/components/ChatPanel.tsx`, `ChatPanel.test.tsx` | `mergePolledMessages (BF-12)` (3 tests) | Poll swaps the server user row into the optimistic message. |
