"""Stop hook: snapshot the memory store into its sidecar repository after each turn.

The harness writes memories asynchronously — once at the end of a turn, and again
whenever background consolidation runs. Without a commit between those writes the
changes pile up and the boundary between "what this turn changed" and "what the last one
did" is lost, which is exactly the boundary anyone reviewing a consolidation needs.

It always exits 0. A hook that fails must not be the reason a session stops, so a
missing store, a missing repository and a git error all come back as
`{"committed": false}` and a zero exit.

Wire it up in ~/.claude/settings.json:

    "Stop": [{"matcher": "*", "hooks": [{"type": "command",
      "command": "<repo>/.venv/bin/python -m flightdeck.memory.hook"}]}]
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.memory import paths, vcs


def main(argv=None) -> int:
    try:
        sys.stdin.read()  # drain the hook payload; nothing in it is needed yet
    except Exception:
        pass

    try:
        override = os.environ.get("FLIGHTDECK_MEMORY_DIR")
        store_dir = Path(override) if override else paths.memory_dir()
        if not store_dir.is_dir() or not vcs.is_initialised(store_dir):
            result = {"committed": False, "files": []}
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            result = vcs.commit(store_dir, f"memory: turn snapshot {stamp}")
    except Exception as e:  # a hook must never break the turn
        result = {"committed": False, "files": [], "error": f"{type(e).__name__}: {e}"}

    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
