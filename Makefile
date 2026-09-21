.PHONY: local-check

# Run the local, non-Docker quality gate. It mirrors the backend CI checks,
# verifies migrations against a disposable SQLite database, then tests and
# builds the frontend. Run it from the repository root with `make local-check`.
local-check:
	ruff check backend/ scripts/
	ruff format --check backend/ scripts/
	@set -e; audit_dir="$$(mktemp -d "$$(pwd)/.alembic-check.XXXXXX")"; \
		trap 'rm -rf "$$audit_dir"' EXIT; \
		export MARKETLENS_DB_OVERRIDE="sqlite:///$$audit_dir/marketlens.db"; \
		DEBUG=false alembic upgrade head; \
		DEBUG=false alembic check
	DEBUG=false REDIS_ENABLED=false OBSERVABILITY_TRACING_ENABLED=false python -m pytest backend/tests/ -q
	npm --prefix frontend test -- --watchAll=false
	npm --prefix frontend run build
