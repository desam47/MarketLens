# =============================================================================
# MarketLens — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install build deps and Python packages into a venv.
# Stage 2 (runtime):  copy the venv into a slim image, expose the API.
# =============================================================================

# ----- Stage 1: builder -----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build essentials are needed only for wheels that compile C extensions
# (numpy / pandas may pull in some, plus aiohttp / yfinance indirect deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy requirements first to leverage Docker layer caching. Installing
# from a file that's stable between source changes means we don't have
# to rebuild the venv on every code change.
COPY backend/requirements.txt /build/requirements.txt
RUN python -m venv /build/.venv \
    && /build/.venv/bin/pip install --upgrade pip \
    && /build/.venv/bin/pip install -r /build/requirements.txt


# ----- Stage 2: runtime -----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

# Runtime libs only. We avoid the builder's build-essential to keep the
# image small. libpq5 / libffi / libssl are common runtime deps for
# SQLAlchemy/asyncpg/cryptography.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libffi8 \
        libssl3 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for the application. Running as root in a
# container is a security anti-pattern — the API should never need
# root privileges.
RUN groupadd --system --gid 1001 marketlens \
    && useradd --system --uid 1001 --gid marketlens marketlens

WORKDIR /app

# Copy the venv from the builder.
COPY --from=builder --chown=marketlens:marketlens /build/.venv /app/.venv

# Copy the application code. The frontend is not part of this image;
# it's expected to be served separately (e.g. nginx, CDN).
COPY --chown=marketlens:marketlens backend /app/backend
COPY --chown=marketlens:marketlens alembic /app/alembic
COPY --chown=marketlens:marketlens alembic.ini /app/alembic.ini
COPY --chown=marketlens:marketlens pyproject.toml /app/pyproject.toml

# Persistent data dir for the SQLite fallback DB. Mount a volume here
# in production. We create it with the correct ownership upfront so
# uvicorn can write to it on first start.
RUN mkdir -p /app/data && chown -R marketlens:marketlens /app/data

USER marketlens

EXPOSE 8000

# Healthcheck — the /api/health endpoint is the source of truth. curl is
# the smallest tool that can talk to it; no need for python here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# Run migrations on startup, then start uvicorn. The migration step is
# guarded — if alembic is configured incorrectly, the container will fail
# fast rather than start with a stale schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.api.main:app --host 0.0.0.0 --port 8000"]
