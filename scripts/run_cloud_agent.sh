#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/src/cloud-agent"

PORT="${APP_PORT:-8000}"
if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
        echo "Port $PORT is in use (PIDs: $PIDS) — stopping leftover listener(s)."
        # shellcheck disable=SC2086
        kill $PIDS 2>/dev/null || true
        sleep 0.5
        STILL="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
        if [ -n "$STILL" ]; then
            # shellcheck disable=SC2086
            kill -9 $STILL 2>/dev/null || true
            sleep 0.3
        fi
    fi
fi

exec uv run uvicorn main:app --reload --host "${APP_HOST:-0.0.0.0}" --port "$PORT"
