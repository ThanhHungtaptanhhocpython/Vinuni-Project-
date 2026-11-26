#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../src/frontend-ui"

if [[ ! -d node_modules || ! -x node_modules/.bin/next ]]; then
  echo "Installing frontend dependencies..."
  npm install
fi

npm run dev
