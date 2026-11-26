#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/src/edge_vla/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/src/edge_vla/.env"
    set +a
fi

# EGL/OSMesa only exist on Linux; macOS MuJoCo renders via CGL. A copied
# .env with MUJOCO_GL=egl would otherwise win over the Darwin default and
# crash with: invalid value for environment variable MUJOCO_GL: egl
ROBOT_MODE="${ROBOT_MODE:-sim}"

if [ "$ROBOT_MODE" != "real" ]; then
    case "$(uname -s)" in
        Darwin)
            case "${MUJOCO_GL:-}" in
                ""|egl|osmesa) export MUJOCO_GL="cgl" ;;
            esac
            ;;
        *)
            if [ -z "${MUJOCO_GL:-}" ]; then
                export MUJOCO_GL="egl"
            fi
            ;;
    esac
fi

if [ -n "${EDGE_VLA_ROBOT_CONFIG:-}" ]; then
    export EDGE_VLA_ROBOT_CONFIG="$(cd "$(dirname "$EDGE_VLA_ROBOT_CONFIG")" && pwd)/$(basename "$EDGE_VLA_ROBOT_CONFIG")"
fi

PORT="${VLA_MCP_PORT:-8931}"
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
        fi
    fi
fi

exec uv run --project "$ROOT_DIR/src/edge_vla" --directory "$ROOT_DIR/src" python -m edge_vla.server
