#!/usr/bin/env bash
# Start `opencode serve` in an empty work directory and the bridge in front of it.
# Usage: ./run.sh            (foreground; Ctrl-C stops both)
#        BRIDGE_PORT=3002 OPENCODE_PORT=4097 ./run.sh
# The work directory holds no files: the agent's read tools see nothing there.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${BRIDGE_WORK:-$HOME/.local/state/opencode-openai-bridge/work}
OPENCODE_PORT=${OPENCODE_PORT:-4097}
export OPENCODE_URL=${OPENCODE_URL:-http://127.0.0.1:$OPENCODE_PORT}

mkdir -p "$WORK"
cp "$HERE/opencode.json" "$WORK/opencode.json"
cd "$WORK"
opencode serve --pure --port "$OPENCODE_PORT" --hostname 127.0.0.1 &
SERVE=$!
trap 'kill $SERVE 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -sf "$OPENCODE_URL/global/health" >/dev/null 2>&1 && break
  sleep 1
done
# Foreground, not exec: when the bridge stops, the trap above stops the server too.
python3 -I "$HERE/bridge.py"
