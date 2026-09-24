# Version 5 Chat Bug Fixes

**Created:** 2026-09-24
**Status:** Open. No fixes applied yet.
**Source:** 2026-09-24 Chat review of `backend/ai/chat.py`, `backend/api/ai/chat_router.py`, `backend/repositories/chat_repository.py`, `frontend/src/components/ChatPanel.tsx`, and `frontend/src/services/api.ts`.
**Related:** [Phase audit](phase_audit_v5.md), [Version 5 plan](v5_plan.md)

"Verified" means the behaviour was reproduced with a throwaway probe test
run under the project's offline pytest guards (`-p backend.tests.conftest`).
"Code-read" means it follows from the code but was not reproduced.

## Summary

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| BF-01 | Critical | Backend | Affirmation prefix confirms destructive actions | Verified | Open |
| BF-02 | High | Backend | Position-size fallback maps numbers by order | Verified | Open |
| BF-03 | High | Backend | Date numbers become calculator inputs | Verified | Open |
| BF-04 | Medium | Backend | Market-metric questions dead-end in the calculator | Verified | Open |
| BF-05 | Medium | API | Positional argument misbinding in Chat router | Verified | Open |
| BF-06 | Medium | Backend | Destructive actions cannot be confirmed with AI off | Code-read | Open |
| BF-07 | Medium | Backend | Delete-alert confirmation does not name the alert | Code-read | Open |
| BF-08 | Medium | Backend | Stream shows unverified model text before verification | Code-read | Open |
| BF-09 | Medium | Backend | Streaming and blocking reply paths have drifted | Code-read | Open |
| BF-10 | Medium | Data | Message ids reused after Clear; feedback reattaches | Verified (live DB) | Open |
| BF-11 | Medium | Backend | Failed action leaves DB session unusable | Code-read | Open |
| BF-12 | Medium | Frontend | Nudge poll duplicates the user's message | Code-read | Open |
| BF-13 | Medium | Full stack | Stream timeout, disconnect, and resend gaps | Code-read | Open |
| BF-14 | Low | Backend | Dotted tickers (BRK.B) rejected | Verified | Open |
| BF-15 | Low | Backend | Add-to-watchlist silently creates a mistyped watchlist | Code-read | Open |
| BF-16 | Low | Backend | Clarification questions recorded as completed steps | Code-read | Open |
| BF-17 | Low | Backend | `_SHARES_RE` slice leaves a literal `s*` | Code-read | Open |
| BF-18 | Low | Tests | Network guard does not block curl_cffi (Yahoo) | Verified | Open |

Suggested fix order: BF-01, BF-02, BF-03, BF-12, BF-05, then the rest.

---

## Critical

### BF-01 — Affirmation prefix confirms destructive actions

**Where:** `backend/ai/chat.py:552` (`_AFFIRM_INTENT`), used at `chat.py:3189` and `chat.py:3212`.

`_AFFIRM_INTENT` only checks how the message starts. After a server
confirmation prompt, if the model returns `action="none"`, the stored
`pending_confirmation` is executed.

**Reproduced:** with a pending `delete_watchlist` for "Tech", these messages deleted it:

- "ok nevermind"
- "okay wait, actually don't"
- "sure, but first show me TSLA"

Only "no" was safe.

**Fix:** accept a confirmation only when the whole message is an
affirmation (e.g. `^\s*(yes|yep|yeah|confirm(ed)?|do it|go ahead)[\s.!]*$`).
Treat any message containing negation or hesitation ("don't", "cancel",
"wait", "never ?mind", "no") as a decline.

**Tests:** confirm-then-decline phrasings must not execute; a bare "yes"
must still execute exactly the pending payload.

---

## High

### BF-02 — Position-size fallback maps numbers by order

**Where:** `chat.py:684` (`position_size`); the same pattern at `chat.py:677` (`risk_reward`).

**Reproduced:** "position size: risk 1% of my 10000 account, entry 50 stop 48"
produced `entry_price=1, stop_price=10000, account_value=50, risk_percent=48`.
The comment at `chat.py:567` says fields are "never from number order".

**Fix:** read each field from its labelled phrase (reuse `_ENTRY_RE`,
`_STOP_RE`, `_TARGET_RE`, `_ACCOUNT_RE`, and add a risk-percent pattern).
If any label is missing, ask for the missing input instead of guessing.

### BF-03 — Date numbers become calculator inputs

**Where:** `chat.py:670`.

**Reproduced:** "What was NVDA's return from Jan 5 to Jan 20?" produced
`percentage_change(old=5, new=20)`, i.e. +300%.

**Fix:** strip date expressions (month names, years, `MM/DD`) before
counting numbers. Skip the two-number fallback when a ticker is present;
route that case to a price-history return tool instead.

---

## Medium

### BF-04 — Market-metric questions dead-end in the calculator

**Where:** `chat.py:2437-2459`, `_CALCULATION_HINT` at `chat.py:561`, `_REUSE_MEMORY_HINT` at `chat.py:566`.

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

**Where:** `backend/api/ai/chat_router.py:766-775` (blocking) and `:864-871` (stream).

**Reproduced:**

- `{regeneration_timeframe: "1h"}` alone reached `answer_chat_message` as
  `chart_state={"timeframe": "1h", "session": None}`.
- `chart_state` plus `regeneration_session` put the scope dict into
  `regeneration_mode`.

The current UI always sends a mode with a scope, so this only affects API
callers.

**Fix:** call `answer_chat_message` / `stream_chat_message` with keyword
arguments.

### BF-06 — Destructive actions cannot be confirmed with AI off

**Where:** the confirmation replay lives only in `_finalize_parsed`
(`chat.py:3186-3198`), which runs only after a model reply. With AI off,
"yes" goes to `_deterministic_context_reply` (`chat.py:2913`), and
`_expire_carried_confirmation` drops the pending action.

**Fix:** check `pending_confirmation` plus a strict affirmation (BF-01)
deterministically, before the model or the AI-off fallback.

### BF-07 — Delete-alert confirmation does not name the alert

**Where:** `_confirm_prompt`, `chat.py:3957`.

The prompt is "Delete that alert? Say yes to confirm." The target id is
model-chosen, so the trader confirms without seeing what will be deleted.

**Fix:** look up the alert and include its name, symbol, condition and
threshold. If the id does not exist, refuse before asking.
The pending `delete_watchlist` has the same weakness: it stores the name,
not the id resolved at prompt time. Store the resolved id so the target
cannot change between prompt and "yes".

### BF-08 — Stream shows unverified model text before verification

**Where:** `_generate_reply_streaming`, `chat.py:3690-3694`.

Deltas are the model's raw `reply` field, streamed before `verify_answer`
and before any action runs. The note at `chat.py:544-551` records that the
model sometimes writes a fake "Done — deleted…"; that text is visible until
the `final` frame overwrites it.

**Fix:** do not stream deltas when the parsed action is not `none`. Render
streamed text as a clearly marked draft until `final` arrives. An
alternative is to buffer until verification passes.

### BF-09 — Streaming and blocking reply paths have drifted

**Where:** `_generate_reply` (`chat.py:2816`) vs `_generate_reply_streaming` (`chat.py:3545`).

- The legacy "no data" degrade in streaming (`chat.py:3573`) lacks the
  watchlist-intent exemption the blocking path has (`chat.py:2853`). In a
  single-ticker session for a ticker with no data, streaming refuses
  "add it to my watchlist".
- On a parse failure, streaming returns `extractor.text` (the raw unparsed
  model reply) as the answer (`chat.py:3771`). Blocking returns "I couldn't
  process that — could you rephrase?"
- Blocking passes `started_at` to the deterministic short-circuit;
  streaming does not, so the turn time budget differs.

**Fix:** have a single generator implementation, with the blocking path
as a thin wrapper that drains it.

### BF-10 — Message ids reused after Clear; feedback reattaches

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

**Where:** `_run_action`, `chat.py:5368`.

The exception is caught but `db.rollback()` is never called. If a flush
failed (e.g. `IntegrityError`), the next `repo.add_message` on the same
session raises `PendingRollbackError`. In the stream path the finalization
fallback uses the same session, so no assistant row is saved.

`answer_chat_message` (`chat.py:1942`) also has no guard after the user
message is saved, so any failure becomes a 500.

**Fix:** roll back in the `except` branch of `_run_action`. Give
`answer_chat_message` the same "always persist one assistant row"
finalization as `stream_chat_message`.

### BF-12 — Nudge poll duplicates the user's message

**Where:** `frontend/src/components/ChatPanel.tsx:302-305` (poll merge), `:386` (final reconcile).

After a turn, only the placeholder is replaced by the server message. The
optimistic user message keeps its negative id. The next 20 s poll sees the
real user row (positive id) as new and appends it after the assistant
reply.

**Fix:** after `final`, replace the optimistic user message with the
server's row, or reload the transcript. Alternatively, have the poll merge
by id and re-sort by `created_at`, dropping optimistic rows the server
already has. Add a ChatPanel test that sends a message and then advances
the poll timer.

### BF-13 — Stream timeout, disconnect, and resend gaps

**Where:** `frontend/src/services/api.ts` (`streamChatMessage`), `ChatPanel.tsx:367`, `chat_router.py:873-875`, `chat.py:1145`.

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

"price of BRK.B" resolves to rejected symbol `BRK` (verified). Browser
position models already allow dots (`chat_router.py:154`). Fix the ticker
extraction in `backend/ai/chat_symbols.py` to keep class suffixes.

### BF-15 — Add-to-watchlist silently creates a mistyped watchlist

`_add_to_watchlist` (`chat.py:4118`) creates a new watchlist when the
named one is not found. A typo creates a stray list. Fix: ask ("No
watchlist called 'Tehc' — create it?") or fuzzy-match existing names.
Symbols rejected as unresolvable are also added (and backfilled) through
the deterministic CRUD path (`chat.py:2780-2790`). Consider requiring a
known ticker, or asking for confirmation, for those.

### BF-16 — Clarification questions recorded as completed steps

The ambiguity replies in `_add_to_watchlist`, `_remove_from_watchlist` and
`_delete_watchlist` return `grounded=True`. `_run_turn_actions` logs them as
`completed`, and a multi-step chain continues. Fix: return `False` (or a
distinct "needs_input" status).

### BF-17 — `_SHARES_RE` slice leaves a literal `s*`

`_NUM[4:]` (`chat.py:576`) strips `\$?\` but leaves `s*`, so the pattern
contains "zero or more literal `s`". Harmless today. Define a separate
unprefixed number pattern instead of slicing.

### BF-18 — Network guard does not block curl_cffi (Yahoo)

`backend/tests/conftest.py` patches `socket.socket.connect`. The Yahoo
provider uses `curl_cffi` (libcurl), which bypasses it. A probe under
pytest received a real Yahoo 404 for `BRK`. Fix: patch
`curl_cffi.requests` in the guard, or stub the Yahoo provider in tests.

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

## Enhancements

1. **One reply path:** a single turn generator for streaming and blocking
   (removes BF-09-class drift).
2. **Server-side completion:** turns finish on the server even if the
   client disconnects, plus a Cancel button in the UI.
3. **Market-metric tools:** period drawdown, volatility, correlation and
   date-range return as tools (unblocks BF-03/BF-04 properly).
4. **Verification-gated streaming:** hold or mark streamed text until
   verification passes (BF-08).
5. **Split `chat.py`:** the module is ~5,400 lines. Split it into intent
   routing, actions, turn orchestration and formatting.

## Fix log

| Date | ID | Commit | Tests | Notes |
|---|---|---|---|---|
| | | | | |
