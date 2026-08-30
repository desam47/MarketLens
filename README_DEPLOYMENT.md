# MarketLens — Deployment Guide

This guide covers deploying MarketLens to production, including Docker,
environment configuration, observability, and recommended infrastructure.

## Table of Contents

- [Quick Start (Docker Compose)](#quick-start-docker-compose)
- [Production Deployment](#production-deployment)
- [Environment Variables](#environment-variables)
- [Observability](#observability)
- [Database](#database)
- [Redis (caching / rate limiting)](#redis-caching--rate-limiting)
- [Reverse Proxy / TLS](#reverse-proxy--tls)
- [Scaling](#scaling)
- [Health Checks](#health-checks)
- [Backup & Recovery](#backup--recovery)

## Quick Start (Docker Compose)

The fastest way to run the full stack locally:

```bash
docker compose up --build
```

This brings up:

| Service    | Port  | Purpose                                          |
|------------|-------|--------------------------------------------------|
| API        | 8000  | FastAPI application                              |
| Redis      | 6379  | Caching, rate limiting, pub/sub                  |
| Jaeger UI  | 16686 | Distributed tracing UI                           |

Verify:

```bash
curl http://localhost:8000/api/health
# → {"status":"healthy",...}

open http://localhost:16686  # Jaeger traces
```

## Production Deployment

### Recommended Architecture

```
                ┌──────────────┐
                │  TLS / WAF   │
                └──────┬───────┘
                       │
                ┌──────▼───────┐
                │   nginx      │  ← TLS termination, static
                │   or Caddy   │
                └──────┬───────┘
                       │
       ┌───────────────┼───────────────┐
       │               │               │
  ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
  │ API #1   │    │ API #2   │    │ API #N   │  ← uvicorn workers
  └────┬─────┘    └────┬─────┘    └────┬─────┘
       └───────────────┼───────────────┘
                       │
       ┌───────────────┼───────────────┐
       │               │               │
  ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
  │  Redis   │    │ Postgres │    │  Jaeger  │
  │  cluster │    │  (RDS)   │    │ (otel)   │
  └──────────┘    └──────────┘    └──────────┘
```

### Build the production image

```bash
docker build -t marketlens:latest .
```

### Run with a managed database

```bash
docker run -d \
  --name marketlens-api \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql://user:pass@db.example.com:5432/marketlens" \
  -e REDIS_ENABLED=true \
  -e REDIS_URL="redis://redis.example.com:6379/0" \
  -e OBSERVABILITY_TRACING_ENABLED=true \
  -e OBSERVABILITY_JAEGER_AGENT_HOST=otel.example.com \
  -e OBSERVABILITY_JAEGER_AGENT_PORT=6831 \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -v marketlens-data:/app/data \
  marketlens:latest
```

### Kubernetes

A bare-bones `Deployment` + `Service` is sufficient. The image is
designed to be stateless — every piece of persistent state lives in
Postgres, Redis, or the shared `/app/data` volume (SQLite fallback).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: marketlens
spec:
  replicas: 3
  selector:
    matchLabels: { app: marketlens }
  template:
    metadata:
      labels: { app: marketlens }
    spec:
      containers:
        - name: api
          image: marketlens:latest
          ports:
            - containerPort: 8000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: { name: marketlens-secrets, key: database-url }
            - name: REDIS_URL
              valueFrom:
                secretKeyRef: { name: marketlens-secrets, key: redis-url }
          readinessProbe:
            httpGet: { path: /api/health, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /api/health, port: 8000 }
            initialDelaySeconds: 15
            periodSeconds: 20
          resources:
            requests: { cpu: "500m", memory: "512Mi" }
            limits: { cpu: "2", memory: "2Gi" }
```

## Environment Variables

All configuration is via environment variables. See `backend/config/settings.py`
for the full schema. Selected highlights:

| Variable                                | Default                    | Purpose                              |
|-----------------------------------------|----------------------------|--------------------------------------|
| `APP_NAME`                              | `MarketLens`               | Service name (used in tracing)       |
| `DEBUG`                                 | `false`                    | Verbose logging                      |
| `HOST` / `PORT`                         | `0.0.0.0` / `8000`         | Bind address                         |
| `DATABASE_URL`                          | `sqlite:///./data/marketlens.db` | SQLAlchemy connection URL     |
| `REDIS_ENABLED`                         | `false`                    | Toggle Redis caching/rate-limiting   |
| `REDIS_URL`                             | `redis://localhost:6379/0` | Redis connection string              |
| `REDIS_PASSWORD`                        | (none)                     | Optional auth                        |
| `MARKET_DATA_PRIMARY_PROVIDER`          | `yahoo_finance`            | Primary market data provider         |
| `MARKET_DATA_FALLBACK_PROVIDERS`        | (empty)                    | Comma-separated fallback chain       |
| `MARKET_DATA_RATE_LIMIT_PER_MINUTE`     | `60`                       | Provider-side rate cap               |
| `AI_ENABLED`                            | `false`                    | Toggle AI integration                |
| `AI_PROVIDER`                           | `ollama`                   | Primary AI provider                  |
| `AI_API_KEY`                            | (none)                     | Provider API key                     |
| `OBSERVABILITY_TRACING_ENABLED`         | `true`                     | OpenTelemetry tracing                |
| `OBSERVABILITY_JAEGER_AGENT_HOST`       | `localhost`                | Jaeger collector / agent host        |
| `OBSERVABILITY_JAEGER_AGENT_PORT`       | `6831`                     | Jaeger port (UDP)                    |
| `OBSERVABILITY_METRICS_ENABLED`         | `true`                     | In-process metrics                   |
| `OBSERVABILITY_STRUCTURED_LOGGING_ENABLED` | `true`                  | JSON logging                         |
| `CORS_ALLOWED_ORIGINS`                  | `http://localhost:3000,http://localhost:5001` | Comma-separated origin allowlist |

**Note on CORS:** never use `*` in production with `allow_credentials=true`;
browsers will silently drop the `Access-Control-Allow-Credentials` header.
Use a comma-separated explicit list.

## Observability

### Structured Logs

Logs are JSON-formatted by default (`OBSERVABILITY_STRUCTURED_LOGGING_ENABLED=true`).
Each line is one event:

```json
{"ts":"2026-08-29T10:00:00.000Z","level":"INFO","logger":"backend.regime",
 "message":"engine updated","symbol":"AAPL","price":313.45,
 "correlation_id":"7d2c-..."}
```

Pipe to your log aggregator (Datadog, CloudWatch, Loki, ELK).

### Distributed Tracing

The app exports OTLP traces to Jaeger (or any OTLP-compatible backend).
Set:

```bash
OBSERVABILITY_TRACING_ENABLED=true
OBSERVABILITY_JAEGER_AGENT_HOST=otel-collector
OBSERVABILITY_JAEGER_AGENT_PORT=6831
```

If the collector is unreachable, tracing is silently disabled — the
app keeps running.

### Correlation IDs

Every request gets a correlation ID. The header `X-Correlation-ID` is
checked first; if absent, a UUID is generated. The ID is:

1. Added to every log line emitted during the request.
2. Echoed back in the response header for client-side debugging.
3. Propagated to outbound HTTP calls (via OTel context).

### Metrics

In-process metrics are exposed at `GET /api/system/performance`:

```json
{
  "scanner_scans_total": 1234,
  "ingestion_bars_total": 567890,
  "http_requests_total": 4321,
  "process_cpu_percent": 12.4,
  "process_memory_rss_mb": 256.0,
  ...
}
```

For Prometheus, run an OTel collector alongside and convert spans → metrics.

## Database

### SQLite (default)

- File: `./data/marketlens.db`
- Good for: single-instance dev, low-write workloads.
- **Not recommended for production.** Use Postgres.

### Postgres (production)

```bash
DATABASE_URL=postgresql://user:pass@db:5432/marketlens
```

The app uses SQLAlchemy 2.0 with sync drivers. Run migrations on startup
(via the CMD in the Dockerfile) or as a separate init container:

```bash
alembic upgrade head
```

## Redis (caching / rate limiting)

When `REDIS_ENABLED=true`, the app uses Redis for:

- **Bar/quote cache** — speeds up hot paths (configurable TTL per category).
- **Rate limiting** — fixed-window counters keyed by client IP.
- **Pub/sub** — `marketlens:bar_updates:{symbol}:{timeframe}` and
  `marketlens:quote_updates:{symbol}` channels.

When Redis is **unavailable**, the app falls back to:

- No caching (always hits the DB).
- In-memory sliding-window rate limiter (per-process; not distributed).
- No pub/sub.

The fallback is **safe by design** — the app keeps working, just slower
and without cross-instance rate limiting.

### Recommended Redis settings

- **Persistence:** AOF + RDB for durability.
- **Memory:** at least 256 MB for moderate workloads; the cache enforces
  `max_bar_keys` / `max_quote_keys` to bound memory.
- **Replication:** use Sentinel or a managed Redis for HA.

## Reverse Proxy / TLS

Put the API behind nginx or Caddy for TLS termination. The Caddy example:

```caddyfile
marketlens.example.com {
    reverse_proxy api:8000
    encode zstd gzip
}
```

Nginx equivalent:

```nginx
server {
    listen 443 ssl http2;
    server_name marketlens.example.com;
    ssl_certificate /etc/letsencrypt/live/marketlens.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/marketlens.example.com/privkey.pem;

    location / {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## Scaling

### Horizontal

The API is stateless. Scale by adding replicas:

```bash
docker compose up --scale api=3
```

The Redis layer is the coordination point — make sure it's a managed
service or a Sentinel-backed cluster.

### Vertical

A single uvicorn worker uses one CPU. Run multiple workers per container:

```bash
uvicorn backend.api.main:app --workers 4 --host 0.0.0.0 --port 8000
```

Rule of thumb: `workers = 2 * CPU cores + 1`.

### Resource Sizing

| Workload             | CPU  | Memory |
|----------------------|------|--------|
| Light (5 users)      | 1    | 512 MB |
| Medium (50 users)    | 2    | 1 GB   |
| Heavy (500+ users)   | 4+   | 4 GB+  |

## Health Checks

| Endpoint                | Purpose                                | Healthy when…                       |
|-------------------------|----------------------------------------|-------------------------------------|
| `GET /api/health`       | Liveness                               | Returns `{"status": "healthy"}`     |
| `GET /api/system/status`| Status (version, config)               | Always 200                          |
| `GET /api/system/performance` | Metrics                           | Always 200                          |

Use `/api/health` for k8s liveness/readiness probes. It's cheap
(no DB calls) and always returns quickly.

## Backup & Recovery

### Database (Postgres)

Use a managed backup (RDS automated backups, Cloud SQL, etc.) or:

```bash
# Daily cron
pg_dump -Fc marketlens > /backups/marketlens-$(date +%F).dump
```

### Redis

Redis is a cache — losing it means a slow rebuild, not data loss. Enable
AOF persistence if you want a tighter recovery point.

### Configuration

Store `.env` files in a secret manager (AWS Secrets Manager, HashiCorp
Vault, etc.). Never commit `.env` to git.

### Disaster Recovery Checklist

1. Provision a fresh Postgres instance.
2. Restore from the most recent `pg_dump`.
3. Spin up the API with the same `DATABASE_URL`, `REDIS_URL`, etc.
4. Run `alembic upgrade head` (idempotent — safe to re-run).
5. Hit `/api/health` and confirm 200.
6. Run a smoke test: `curl http://api/api/market-data/quote/AAPL`.

## Troubleshooting

### "Redis connection refused"

Redis isn't running, or `REDIS_URL` is wrong. The app continues with
the in-memory fallback — check logs to confirm.

### "Trace export failed"

OTel collector is unreachable. Tracing silently degrades. Confirm
`OBSERVABILITY_JAEGER_AGENT_HOST` / `PORT` are correct.

### "429 Too many requests"

Rate limit exceeded. Either:
- The client is too aggressive (reduce request rate).
- The limit is too tight (raise `RATE_LIMIT_MAX_REQUESTS_PER_WINDOW`).

### "Database is locked"

SQLite in WAL mode under concurrent writes. Switch to Postgres for
write-heavy workloads.

### "No bars returned"

Yahoo Finance may be rate-limiting. The manager has built-in retries
and falls back to alternative providers if configured.
