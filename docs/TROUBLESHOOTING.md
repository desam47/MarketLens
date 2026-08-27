# Troubleshooting Guide

## Backend

### Application fails to start

**Error**: `ModuleNotFoundError: No module named 'backend'`

**Cause**: The `backend/` package is not on `sys.path`. This can happen if you run `run.py` from outside the project root.

**Fix**: Always run from the project root:
```bash
cd /path/to/MarketLens
python3 backend/run.py
```

Alternatively, use the provided runner that sets up paths correctly:
```bash
python3 backend/run.py
```

---

**Error**: `Table 'quotes' is already defined`

**Cause**: The `backend.models` package is being loaded twice via different import paths (e.g., `from backend.models` and `from models`), causing SQLAlchemy to re-register the same table. This is a test-only issue caused by inconsistent `sys.path` in test files.

**Fix**: Test files must use the `backend.` prefix for all imports:
```python
# Correct
from backend.models.watchlist import Watchlist
# Wrong — creates a second top-level 'models' package
from models.watchlist import Watchlist
```

The test `backend/tests/watchlist/test_watchlist_repository.py` has this bug and is currently skipped.

---

**Error**: `InvalidRequestError: Table 'quotes' is already defined for this MetaData instance`

**Cause**: Same as above, but in production code if there are duplicate imports.

**Fix**: Verify all code uses `from backend.X import Y` style imports. Do not mix with bare `from X import Y` for backend packages.

### Market data errors

**Error**: `Quote not found for symbol XYZ`

**Cause**: Symbol not recognized by the current provider, or market is closed.

**Fix**:
1. Verify the symbol format (e.g., `AAPL`, `BTC-USD` — not `AAPL.US`)
2. Check provider status: `SELECT * FROM provider_status ORDER BY timestamp DESC LIMIT 5;`
3. Try switching to Yahoo Finance: `MARKET_DATA_PRIMARY_PROVIDER=yahoo_finance`

---

**Error**: `Rate limit exceeded` from Alpha Vantage

**Cause**: Exceeded the free tier (25 req/day) or premium tier limits.

**Fix**:
1. Wait for the rate limit window to reset
2. Add a fallback: `MARKET_DATA_FALLBACK_PROVIDERS=yahoo_finance`
3. Upgrade to a paid Alpha Vantage plan for higher limits

### Database errors

**Error**: `sqlite3.OperationalError: database is locked`

**Cause**: Another process is holding an SQLite write lock.

**Fix**:
1. Close all other processes using the database
2. For development, use `SQLite URL=sqlite:///./marketlens.db` (single writer)
3. For production with concurrent access, migrate to PostgreSQL

---

**Error**: `No such table: watchlists`

**Cause**: Database not initialized.

**Fix**: Run the database initialization script:
```bash
python3 scripts/init_db.py
```

Or let FastAPI auto-create tables on startup (dev mode only):
```bash
DEBUG=true python3 scripts/run.py
```

### API errors

**Error**: `422 Unprocessable Entity` on POST requests

**Cause**: Request body doesn't match the expected Pydantic model.

**Fix**: Check the request body against the API schema at `http://localhost:8000/docs`. Use the "Try it out" feature to see the exact expected format.

---

**Error**: `429 Too Many Requests`

**Cause**: Rate limit exceeded on write endpoints (max 30 requests per IP per 60 seconds).

**Fix**: Implement client-side request throttling. Read endpoints are not rate-limited.

---

**Error**: `CORS policy blocked` in browser console

**Cause**: The frontend origin is not in `CORS_ALLOWED_ORIGINS`.

**Fix**: Add the frontend origin to `CORS_ALLOWED_ORIGINS` in `backend/.env`:
```bash
CORS_ALLOWED_ORIGINS=http://localhost:3000,https://app.example.com
```

---

## Frontend

### Dashboard shows no data

1. Verify the backend is running: `curl http://localhost:8000/api/health`
2. Check browser console for network errors
3. Verify CORS is configured correctly (see above)

### React build fails

```bash
cd frontend
npm install
npm run build
```

If `npm install` fails, check Node.js version (requires Node 18+):
```bash
node --version  # Should be >= 18.0.0
```

## Performance

### Slow indicator calculations

Large lookback windows (e.g., `period=200` for EMA) are O(n) per update. For real-time streaming, keep lookback windows reasonable.

### High memory usage

SQLite stores all bars in-process. For long lookback periods across many symbols, consider:
1. Archiving old bars to a separate table or cold storage
2. Switching to PostgreSQL for production scale
3. Using the `DATABASE_URL` env var to point to a managed database

## Getting Help

1. Check `http://localhost:8000/docs` for the full API reference
2. Run with `DEBUG=true` for verbose JSON logging
3. Check `marketlens.db` (SQLite) directly: `sqlite3 marketlens.db ".schema"`
