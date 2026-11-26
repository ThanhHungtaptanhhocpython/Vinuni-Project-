#!/usr/bin/env bash
set -euo pipefail
set -m  # job control: each backgrounded job gets its own process group,
        # so "kill -TERM -$pid" below reaches its children too — the
        # portable substitute for Linux's setsid (not present on macOS).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=()
NAMES=()
STOPPING=0

cleanup() {
    if [ "$STOPPING" -eq 1 ]; then
        return
    fi
    STOPPING=1

    echo
    echo "Stopping servers..."

    for i in "${!PIDS[@]}"; do
        local pid="${PIDS[$i]}"
        local name="${NAMES[$i]}"
        if kill -0 "-${pid}" 2>/dev/null; then
            echo "Stopping ${name}..."
            kill -TERM "-${pid}" 2>/dev/null || true
        elif kill -0 "$pid" 2>/dev/null; then
            echo "Stopping ${name}..."
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    sleep 2

    for i in "${!PIDS[@]}"; do
        local pid="${PIDS[$i]}"
        local name="${NAMES[$i]}"
        if kill -0 "-${pid}" 2>/dev/null; then
            echo "Force stopping ${name}..."
            kill -KILL "-${pid}" 2>/dev/null || true
        elif kill -0 "$pid" 2>/dev/null; then
            echo "Force stopping ${name}..."
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done

    wait 2>/dev/null || true
}

on_signal() {
    cleanup
    exit 130
}

RESET='\033[0m'
BOLD='\033[1m'
CYAN='\033[36m'
YELLOW='\033[33m'
GREEN='\033[32m'

if [ -n "${NO_COLOR:-}" ] || [ ! -t 1 ]; then
    RESET=''
    BOLD=''
    CYAN=''
    YELLOW=''
    GREEN=''
fi

color_for() {
    case "$1" in
        cloud-agent) printf '%s' "$CYAN" ;;
        edge-vla) printf '%s' "$YELLOW" ;;
        frontend) printf '%s' "$GREEN" ;;
        *) printf '%s' "$BOLD" ;;
    esac
}

prefix_lines() {
    local tag="$1"
    local color
    color="$(color_for "$tag")"
    while IFS= read -r line || [ -n "${line}" ]; do
        printf '%b[%s]%b %s\n' "${BOLD}${color}" "$tag" "$RESET" "$line"
    done
}

start_server() {
    local name="$1"
    local script="$2"

    echo "Starting ${name}..."
    # Keep $! as the server script so cleanup can still kill its process group.
    # Merge stderr and prefix every line so mixed logs show their source.
    PYTHONUNBUFFERED=1 "${ROOT_DIR}/scripts/${script}" \
        > >(prefix_lines "$name") 2>&1 &
    PIDS+=("$!")
    NAMES+=("$name")
}

# Portable substitute for `wait -n` (bash >=4.3 only; macOS ships bash 3.2):
# poll until any tracked PID has exited.
wait_any() {
    while true; do
        for pid in "${PIDS[@]}"; do
            if ! kill -0 "$pid" 2>/dev/null; then
                return
            fi
        done
        sleep 1
    done
}

trap cleanup EXIT
trap on_signal INT TERM

start_server "cloud-agent" "run_cloud_agent.sh"
start_server "edge-vla" "run_edge_vla.sh"
start_server "frontend" "run_frontend.sh"

echo
echo "Servers are starting:"
echo "- Cloud agent: http://localhost:${APP_PORT:-8000}"
echo "- Edge VLA MCP: http://localhost:${VLA_MCP_PORT:-8931}"
echo "- Frontend UI: http://localhost:3000"
echo
echo "Press Ctrl+C to stop all servers."

wait_any
