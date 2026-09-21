#!/usr/bin/env bash
# Start the local backend, frontend, and optional workers from the repository
# root. Configuration is read without sourcing .env, so credential values are
# never evaluated as shell code.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

config_value() {
    local key="$1"
    local default_value="$2"
    local value="${!key:-}"

    if [[ -z "$value" && -f "$SCRIPT_DIR/.env" ]]; then
        value="$(awk -v key="$key" '
            index($0, key "=") == 1 {
                sub(/^[^=]*=/, "")
                sub(/\r$/, "")
                print
                exit
            }
        ' "$SCRIPT_DIR/.env")"
    fi

    printf '%s' "${value:-$default_value}"
}

file_config_value() {
    local key="$1"
    local default_value="$2"
    local value=""
    if [[ -f "$SCRIPT_DIR/.env" ]]; then
        value="$(awk -v key="$key" '
            index($0, key "=") == 1 {
                sub(/^[^=]*=/, "")
                sub(/\r$/, "")
                print
                exit
            }
        ' "$SCRIPT_DIR/.env")"
    fi
    printf '%s' "${value:-$default_value}"
}

BACKEND_HOST="$(config_value HOST "0.0.0.0")"
BACKEND_PORT="$(config_value PORT "5001")"
FRONTEND_PORT="$(config_value FRONTEND_PORT "3000")"
STARTUP_MODE="$(config_value STARTUP_MODE "full")"
DEBUG_VALUE="$(file_config_value DEBUG "false")"
validate_port() {
    local name="$1"
    local value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]] || ((value < 1 || value > 65535)); then
        echo "$name must be an integer between 1 and 65535, got '$value'." >&2
        exit 1
    fi
}
validate_port "PORT" "$BACKEND_PORT"
validate_port "FRONTEND_PORT" "$FRONTEND_PORT"
case "$(printf '%s' "$STARTUP_MODE" | tr '[:upper:]' '[:lower:]')" in
    full|api) STARTUP_MODE="$(printf '%s' "$STARTUP_MODE" | tr '[:upper:]' '[:lower:]')" ;;
    *)
        echo "STARTUP_MODE must be 'full' or 'api', got '$STARTUP_MODE'." >&2
        exit 1
        ;;
esac
case "$(printf '%s' "$DEBUG_VALUE" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) DEBUG_VALUE=true ;;
    0|false|no|off) DEBUG_VALUE=false ;;
    *)
        echo "DEBUG must be a boolean, got '$DEBUG_VALUE'." >&2
        exit 1
        ;;
esac
if [[ "$BACKEND_PORT" == "$FRONTEND_PORT" ]]; then
    echo "PORT and FRONTEND_PORT must use different values." >&2
    exit 1
fi

DISPLAY_HOST="$BACKEND_HOST"
if [[ "$DISPLAY_HOST" == "0.0.0.0" || "$DISPLAY_HOST" == "::" ]]; then
    DISPLAY_HOST="localhost"
elif [[ "$DISPLAY_HOST" == *:* && "$DISPLAY_HOST" != \[* ]]; then
    DISPLAY_HOST="[$DISPLAY_HOST]"
fi

BACKEND_URL="http://$DISPLAY_HOST:$BACKEND_PORT"
API_BASE_URL="$(config_value REACT_APP_API_BASE_URL "$BACKEND_URL/api")"
REDIS_URL="$(config_value REDIS_URL "redis://localhost:6379/0")"
REDIS_ENABLED="$(config_value REDIS_ENABLED "true")"
PIDS=()

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    echo "\n🛑 Shutting down..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    wait "${PIDS[@]}" 2>/dev/null || true
    exit "$status"
}

trap cleanup EXIT
trap 'exit 0' INT TERM

echo "📁 Working directory: $SCRIPT_DIR"
echo "🚀 Starting backend on $BACKEND_URL ..."
# Reload excludes tests so editing them does not restart the whole application.
DEBUG="$DEBUG_VALUE" STARTUP_MODE="$STARTUP_MODE" python -m uvicorn backend.api.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --reload \
    --reload-exclude "$SCRIPT_DIR/backend/tests" &
BACKEND_PID=$!
PIDS+=("$BACKEND_PID")

if [[ -d "frontend" && -f "frontend/package.json" ]]; then
    if command -v npm >/dev/null 2>&1; then
        echo "🎨 Starting frontend on http://localhost:$FRONTEND_PORT (API: $API_BASE_URL) ..."
        (
            cd frontend
            BROWSER=none PORT="$FRONTEND_PORT" REACT_APP_API_BASE_URL="$API_BASE_URL" npm start
        ) &
        PIDS+=("$!")
    else
        echo "⚠️  npm was not found — backend is running without the frontend."
    fi
fi

case "$(printf '%s' "$REDIS_ENABLED" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
        if [[ "$STARTUP_MODE" == "api" ]]; then
            echo "ℹ️  STARTUP_MODE=api — skipping background workers."
        elif command -v rq >/dev/null 2>&1; then
            echo "🚀 Starting AI analysis worker (marketlens-workers) ..."
            DEBUG="$DEBUG_VALUE" rq worker --url "$REDIS_URL" --worker-class rq.worker.SimpleWorker marketlens-workers &
            PIDS+=("$!")
            echo "🚀 Starting backfill worker (marketlens-backfill x1) ..."
            DEBUG="$DEBUG_VALUE" rq worker --url "$REDIS_URL" --worker-class rq.worker.SimpleWorker marketlens-backfill &
            PIDS+=("$!")
        else
            echo "⚠️  'rq' CLI not found — skipping background workers."
        fi
        ;;
    *)
        echo "ℹ️  Redis is disabled — skipping background workers."
        ;;
esac

echo ""
echo "✅ MarketLens started!"
echo "   Backend:  $BACKEND_URL"
echo "   Frontend: http://localhost:$FRONTEND_PORT"
echo "   API Docs: $BACKEND_URL/docs"
echo "Press Ctrl+C to stop all services."

wait "$BACKEND_PID"
