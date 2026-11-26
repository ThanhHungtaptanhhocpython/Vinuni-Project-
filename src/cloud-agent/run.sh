#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
uvicorn main:app --reload --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}"
