#!/usr/bin/env python3
"""The `flightdeck` MCP server — a wrapper that spawns the CLI per call.

This file is deliberately DUMB, and its dumbness is the feature. An MCP server is
spawned once per Claude session and then runs that code for days; on 2026-08-07 alone a
long-lived server refused a field added that morning and never saw a guard added that
afternoon. So the tool logic does not live here: every `tools/call` spawns
`flightdeck.cli` in a fresh process, which always runs the code on disk, and every
`tools/list` asks the CLI for its schemas the same way. The only thing that can go stale
is this framing loop — ~90 lines that have no reason to change when tools do.

The price is one process spawn plus one database connect per call. Measured at well
under a second, which an interactive tool call never notices; what it buys is that a fix
to any tool reaches every running session on the next call, with no restart.

It intentionally imports nothing from `flightdeck` — the moment it did, that import
would be the stale copy.
"""
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
_CLI = [sys.executable, "-m", "flightdeck.cli"]


def _cli(args: list[str], stdin: str | None = None) -> str:
    """Run the CLI, return its stdout. The CLI's contract is one JSON document on
    stdout whatever happens, so a broken pipe or a crash is the only thing to wrap."""
    try:
        proc = subprocess.run(_CLI + args, cwd=str(BACKEND), input=stdin,
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "the tool call timed out after 120s"})
    if proc.stdout.strip():
        return proc.stdout.strip()
    tail = (proc.stderr or "").strip().splitlines()[-3:]
    return json.dumps({"error": f"the CLI produced no output (exit {proc.returncode})",
                       "stderr": tail})


def handle(req: dict):
    mid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "flightdeck", "version": "0.2"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        # Fresh from the CLI, so a tool added this afternoon is advertised to a session
        # started this morning the next time the harness re-lists.
        try:
            tools = json.loads(_cli(["--schemas"]))
        except ValueError:
            tools = []
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        out = _cli([name, "--json", "-"], stdin=json.dumps(args, ensure_ascii=False))
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": out}]}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
