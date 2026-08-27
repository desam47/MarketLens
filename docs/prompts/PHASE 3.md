# PHASE 3 — WATCHLIST SYSTEM

Implement watchlist management.

Do not implement advanced trend analysis yet.

Users must be able to:

- create watchlist

- rename watchlist

- delete watchlist

- add symbol

- remove symbol

- reorder symbols

- enable/disable symbol

- search symbols

- import/export watchlist

Database entities should include:

Watchlist

WatchlistSymbol

Do not hard-code any personal watchlist.

Create APIs:

GET /api/watchlists

POST /api/watchlists

GET /api/watchlists/{id}

PUT /api/watchlists/{id}

DELETE /api/watchlists/{id}

and symbol operations.

Frontend:

Create a usable watchlist management page.

Allow users to enter:

AAPL

NVDA

MSFT

SPY

QQQ

etc.

Validate symbols where possible.

Do not assume every symbol exists.

Add tests.

After completion verify:

1. Create watchlist.

2. Add symbols.

3. Remove symbols.

4. Reload application.

5. Confirm persistence.

Then STOP.

