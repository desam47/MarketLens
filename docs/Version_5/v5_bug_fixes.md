# Version 5 Chat Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (batch 4: BF-14 to BF-18, BF-20)
**Status:** In progress. Batches 1 to 4 are committed (batch 3's migration is applied to the live DB).
**Scorecard:** 15 ✅ COMPLETE, 1 ⚠️ PARTIAL, 4 ❌ NOT STARTED, 0 🟡 DEFERRED.
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

Line numbers refer to the code as of batch 4. Each batch shifts
`chat.py` (batch 1 added about 40 lines near the top, batch 2 about 120
more, batch 4 about 30), so line numbers quoted in older notes or commits
will not match.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| BF-01 | Critical | Backend | Affirmation prefix confirms destructive actions | Verified | ✅ COMPLETE |
| BF-02 | High | Backend | Position-size fallback maps numbers by order | Verified | ✅ COMPLETE |
| BF-03 | High | Backend | Date numbers become calculator inputs | Verified | ⚠️ PARTIAL |
| BF-04 | Medium | Backend | Market-metric questions dead-end in the calculator | Verified | ❌ NOT STARTED |
| BF-05 | Medium | API | Positional argument misbinding in Chat router | Verified | ✅ COMPLETE |
| BF-06 | Medium | Backend | Destructive actions cannot be confirmed with AI off | Code-read | ✅ COMPLETE |
| BF-07 | Medium | Backend | Delete-alert confirmation does not name the alert | Code-read | ✅ COMPLETE |
| BF-08 | Medium | Backend | Stream shows unverified model text before verification | Code-read | ❌ NOT STARTED |
| BF-09 | Medium | Backend | Streaming and blocking reply paths have drifted | Code-read | ❌ NOT STARTED |
| BF-10 | Medium | Data | Message ids reused after Clear; feedback reattaches | Verified (live DB) | ✅ COMPLETE |
| BF-11 | Medium | Backend | Failed action leaves DB session unusable | Code-read | ✅ COMPLETE |
| BF-12 | Medium | Frontend | Nudge poll duplicates the user's message | Code-read | ✅ COMPLETE |
| BF-13 | Medium | Full stack | Stream timeout, disconnect, and resend gaps | Code-read | ❌ NOT STARTED |
| BF-14 | Low | Backend | Dotted tickers (BRK.B) rejected | Verified | ✅ COMPLETE |
| BF-15 | Low | Backend | Add-to-watchlist creates a new list for a mistyped or differently-cased name | Verified | ✅ COMPLETE |
| BF-16 | Low | Backend | Clarification questions recorded as completed steps | Code-read | ✅ COMPLETE |
| BF-17 | Low | Backend | `_SHARES_RE` slice leaves a literal `s*` | Code-read | ✅ COMPLETE |
| BF-18 | Low | Tests | Network guard does not block curl_cffi (Yahoo) | Verified | ✅ COMPLETE |
| BF-19 | Low | Frontend | Evidence card lost its 8-item cap; two ChatPanel tests failing | Verified | ✅ COMPLETE |
| BF-20 | Low | Tests | Two context tests expect an inferred "live" quote status | Verified | ✅ COMPLETE |

**Next suggested order:**

1. BF-04 (also finishes BF-03).
2. The streaming group: BF-08, BF-09, BF-13.

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
**Where:** `chat.py:2472-2494` (in `_build_deterministic_chat_reply`), `_CALCULATION_HINT` at `chat.py:570`, `_REUSE_MEMORY_HINT` at `chat.py:575`.

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

**Status:** ❌ NOT STARTED
**Where:** `_generate_reply_streaming`, `chat.py:3801-3805`.

Deltas are the model's raw `reply` field, streamed before `verify_answer`
and before any action runs. The note at `chat.py:544-551` records that the
model sometimes writes a fake "Done — deleted…"; that text is visible until
the `final` frame overwrites it.

**Fix:** do not stream deltas when the parsed action is not `none`. Render
streamed text as a clearly marked draft until `final` arrives. An
alternative is to buffer until verification passes.

### BF-09 — Streaming and blocking reply paths have drifted

**Status:** ❌ NOT STARTED
**Where:** `_generate_reply` (`chat.py:2903`) vs `_generate_reply_streaming` (`chat.py:3649`).

- **No-data watchlist exemption:** the legacy "no data" degrade in
  streaming (`chat.py:3684`) lacks the watchlist-intent exemption the
  blocking path has (`chat.py:2946`). In a single-ticker session for a
  ticker with no data, streaming refuses "add it to my watchlist".
- **Parse failures:** streaming returns `extractor.text` (the raw
  unparsed model reply) as the answer (`chat.py:3882`). Blocking returns
  "I couldn't process that — could you rephrase?"
- **Time budget:** blocking passes `started_at` to the deterministic
  short-circuit; streaming does not, so the turn time budget differs.

**Fix:** have a single generator implementation, with the blocking path
as a thin wrapper that drains it.

**Progress:** batch 2 moved the finishing half of a turn (verification,
blocks, persistence) into a shared `_finish_turn` (BF-11), and the
pending-confirmation check is shared (BF-06). The three drifts above are
in the generation half and are still open.

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
- Alert-scoped sessions never poll, so they were never affected.

### BF-13 — Stream timeout, disconnect, and resend gaps

**Status:** ❌ NOT STARTED
**Where:** `frontend/src/services/api.ts` (`streamChatMessage`), `ChatPanel.tsx:388`, `chat_router.py:869-871`, `chat.py:1187`.

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

**Follow-up (unverified):** the providers pass the symbol through as
given. Alpaca and Finnhub use `BRK.B`, but Yahoo expects `BRK-B`, so a
Yahoo-only quote for a class share may still miss. This was not checked
against live providers.

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

**Follow-up:** a clarification step shows as `failed` in the step trace
(⚠). A distinct `needs_input` status would read better, but the handlers'
`(text, grounded)` return can't tell a question from a failure without a
wider change.

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

- **Clear without confirmation:** the Clear button deletes the universal
  chat history with no confirmation. `DELETE /api/ai/chat/sessions` with
  no filter wipes every session.
- **Blocking query in notebook save:** `save_notebook_item`
  (`chat_router.py:660`) runs a synchronous DB query on the event loop.
- **Unchecked model symbol lists:** the notebook save walks
  `symbols.verified/partial/unavailable` without checking they are lists,
  so a string would be split into characters.
- **Existing lint errors:** three `ruff` errors are also present in the
  committed files. They are auto-fixable and unrelated to these fixes.
  - `backend/api/ai/chat_router.py:186` UP037: quoted forward reference
    `"BrowserScanFilter"`.
  - `backend/tests/ai/test_chat_calculation.py:1` and
    `backend/tests/ai/test_chat.py:15` I001: import block not sorted.

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

## Verification

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

The full backend suite was not run.

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
- **Batch 4:** commit `fix(chat): resolve class tickers and watchlist names, stop chains on questions`, on `development`.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | BF-01 | ✅ COMPLETE | batch 1 | `backend/ai/chat.py` | `test_chat_actions.py::TestConfirmationAffirmation` | `_AFFIRM_INTENT` matches only a whole-message affirmation. |
| 2026-09-24 | BF-02 | ✅ COMPLETE | batch 1 | `backend/ai/chat.py` | `test_chat_calculation.py` (3 tests) | Labelled-field parsing; new `_RISK_PERCENT_RE`, `_ACCOUNT_BEFORE_RE`. |
| 2026-09-24 | BF-03 | ⚠️ PARTIAL | batch 1 | `backend/ai/chat.py` | `test_chat_calculation.py` (2 tests) | New `_CALC_DATE_RE`; the fallback steps aside when a date is present. |
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
