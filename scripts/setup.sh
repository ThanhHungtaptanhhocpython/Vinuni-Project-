#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=== P-172 setup ==="

python3 -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env"
fi

if [ ! -f src/cloud-agent/.env ]; then
    cp src/cloud-agent/.env.example src/cloud-agent/.env
    echo "Created src/cloud-agent/.env"
fi

if [ ! -f src/frontend-ui/.env.local ]; then
    cp src/frontend-ui/.env.example src/frontend-ui/.env.local
    echo "Created src/frontend-ui/.env.local"
fi

echo
echo "--- PostgreSQL (docker compose) ---"
if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found — start Postgres yourself, then set DATABASE_URL in src/cloud-agent/.env"
else
    docker compose up -d postgres
    echo -n "Waiting for Postgres"
    for _ in $(seq 1 30); do
        if docker compose exec -T postgres pg_isready -U postgres -d p172 >/dev/null 2>&1; then
            echo " — ready"
            break
        fi
        echo -n "."
        sleep 1
    done
    if ! docker compose exec -T postgres pg_isready -U postgres -d p172 >/dev/null 2>&1; then
        echo
        echo "WARN: Postgres did not become ready in time. Check: docker compose ps"
    else
        docker compose exec -T postgres psql -U postgres -tc \
            "SELECT 1 FROM pg_database WHERE datname = 'p172_test'" \
            | grep -q 1 \
            || docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE p172_test"
        echo "Ensured database p172_test (pytest)"
    fi
fi

echo
echo "--- cloud-agent deps ---"
(cd src/cloud-agent && uv sync --extra dev)

echo
echo "--- edge_vla deps (first sync can take a while) ---"
(cd src/edge_vla && uv sync)

echo
echo "--- frontend deps ---"
(cd src/frontend-ui && npm install)

echo
echo "Setup complete."
echo "  1. Fill secrets in src/cloud-agent/.env (and .env.local if needed)"
echo "  2. Run ./scripts/run_all_servers.sh"
