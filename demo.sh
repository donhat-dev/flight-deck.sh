#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

# load local secrets (TOKEN_AUDIT_DATABASE_URL, …) from the gitignored .env
set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a

# venv lives at the repo root; deps come from python/requirements.txt
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install -q -r python/requirements.txt

# Package now lives in python/flightdeck, so run uvicorn with cwd=python.
# Pin workspace root + graph file: the package sits a level deeper, so its
# relative-path fallbacks would otherwise resolve to the flight-deck.sh root.
export FLIGHTDECK_WORKSPACE=/home/nathando/Documents/Projects
export TOKEN_AUDIT_GRAPH_FILE=/home/nathando/Documents/Projects/nakivo-graph/nakivo-graph.json

# backend (initial ingest runs on startup; watcher keeps it warm; --reload picks up code edits)
( cd python && TOKEN_AUDIT_CONFIG=config.toml \
  "$ROOT/.venv/bin/uvicorn" flightdeck.server:app --host 127.0.0.1 --port 8010 \
  --reload --reload-dir flightdeck --timeout-graceful-shutdown 2 ) &
BACK=$!
echo "backend  http://127.0.0.1:8010  (pid $BACK)"

# frontend dev server
( cd clients/web && npm install --silent && npm run dev ) &
FRONT=$!
echo "frontend http://localhost:5190  (pid $FRONT)"

trap 'kill $BACK $FRONT 2>/dev/null || true' INT TERM
wait
