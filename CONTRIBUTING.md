# Contributing to MarketLens

Thanks for your interest in contributing! This document covers everything you
need to get the project running locally, run the test suite, and submit a
change.

## Development Setup

### Prerequisites

- **Python 3.12+** (the project targets 3.12; older versions are not tested)
- **Git**
- Optional but recommended: **Redis 7+** for caching / rate limiting / pub/sub
- Optional: **Jaeger** (via the bundled binary or Docker) for tracing
- Optional: **Docker + docker compose** for the full local stack

### First-time setup

```bash
# Clone and enter the repo
git clone <repo-url> marketlens
cd marketlens

# Create a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install runtime + dev deps
pip install --upgrade pip
pip install -e ".[dev]"

# Install pre-commit hooks (runs ruff on every commit)
pip install pre-commit
pre-commit install
```

### Run the API locally

```bash
# From the repo root
uvicorn backend.api.main:app --reload --port 8000
```

Open <http://localhost:8000/docs> for the OpenAPI explorer. Health:
<http://localhost:8000/api/health>.

### Run the full local stack (API + Redis + Jaeger)

```bash
docker compose up --build
```

- API: <http://localhost:8000>
- Jaeger UI: <http://localhost:16686>

## Code Style

We use **Ruff** for both linting and formatting. Configuration lives in
`pyproject.toml`. The pre-commit hook runs both, so the formatting should
already match before you push.

Useful commands:

```bash
# Lint
ruff check backend/ scripts/

# Auto-format in place
ruff format backend/ scripts/
```

We follow PEP 8 with a few project-specific rules:

- Line length: 100 chars.
- Imports: `isort` ordering (handled by ruff).
- Modern syntax: `pyupgrade` is on — use `list[...]`, `dict[...]`, f-strings,
  `match` where it improves clarity.
- Type hints are encouraged on public functions; tests don't need them.

## Testing

We use `pytest`. The test suite is in `backend/tests/`.

```bash
# All tests
pytest backend/tests/

# One file
pytest backend/tests/api/test_cache_middleware.py -v

# One test
pytest backend/tests/api/test_cache_middleware.py::TestCacheMiddleware::test_get_response_gets_etag

# Stop on first failure
pytest -x backend/tests/

# With coverage (if pytest-cov is installed)
pytest --cov=backend backend/tests/
```

### Test categories

- `backend/tests/api/` — HTTP / middleware / rate limiter / cache tests
- `backend/tests/market_data/` — provider, cache, manager tests
- `backend/tests/observability/` — metrics, tracing tests
- `backend/tests/engines/` — regime, trend, multi-timeframe engine tests
- `backend/tests/scanner/` — scanner / WebSocket tests

### Writing tests

- Use `unittest.TestCase` or `pytest` style — both are accepted. We lean
  toward `unittest` for parity with the existing test files.
- Mock external services. The suite is offline-first: no live HTTP calls
  to Yahoo, no live Redis required, no live Postgres. Anything that
  requires a live dependency is gated behind an integration marker.
- Tests should be **isolated** — the `conftest.py` resets the in-process
  rate limiter before each test, so don't rely on cross-test state.
- Name tests descriptively: `test_<what>_<expected_outcome>`.

## Pull Request Process

1. **Branch off `main`** (or `develop` if the repo uses gitflow).
2. **Make focused commits.** One logical change per commit.
3. **Add or update tests** for any behaviour change.
4. **Run the full test suite** before opening a PR:
   ```bash
   pytest backend/tests/
   ```
5. **Open a PR** with a clear description:
   - What changed
   - Why
   - How you tested it
   - Any follow-up work
6. **Address review feedback** by pushing new commits. Don't force-push
   during review — it makes re-review harder.

## Architecture Notes

MarketLens has four layers:

1. **Providers** (`backend/market_data/providers/`) — pluggable data
   sources. New providers register in `_PROVIDER_CLASSES`.
2. **Manager** (`backend/market_data/services/manager.py`) — orchestrates
   providers, fallback, Redis caching, pub/sub.
3. **Engines** (`backend/engines/`, `backend/regime/`, `backend/trend/`,
   etc.) — compute signals and indicators from the bar/quote stream.
4. **API** (`backend/api/`) — FastAPI routers, middleware, structured
   logging, tracing.

When adding a feature, place the code in the layer that matches its
responsibility. **Cross-layer leakage is a code smell**: e.g. an
endpoint that calls a provider directly bypasses the manager's caching
and fallback — bad.

## Coding Principles

These come from `docs/Version_1/phase_audit_v1.md` and apply broadly:

- **Configuration over hard-coding.** Every magic number is in `settings.py`.
- **Fallback-safe.** If Redis is down, the API still serves. If the primary
  data provider is down, the fallback chain kicks in. If tracing is
  disabled, the API still starts.
- **Structured logging.** Use `extra={...}` to attach context, not
  string formatting inside the message.
- **Versioned strategies.** Any change to signal generation bumps
  `trend.strategy_version` so prior backtest results stay comparable.
- **No silent failures.** If a primary provider fails, the user sees
  the fallback provider's name in the response.

## Reporting Issues

When filing a bug, include:

- What you did (curl command, UI action)
- What you expected
- What happened (logs, error message, screenshot)
- Environment (Python version, OS, whether Redis is running)

For security issues, **don't open a public issue** — see the security
policy in the repo root.
