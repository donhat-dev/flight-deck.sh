#!/usr/bin/env python3
"""`flightdeck` — the agent CLI, and the BASE every other surface wraps.

    flightdeck radar_get --slug flightdeck
    flightdeck radar_move --json - <<'JSON'
    {"slug": "flightdeck", "num": 13, "ring": "adopt", "period": "Q4 2026",
     "why": "…", "session_id": "…"}
    JSON
    flightdeck --list · --schemas · --schema radar_move · --version

Why a CLI is the base and not the MCP server: a spawned process always runs the code on
disk. The stdio MCP servers are spawned once per Claude session, so on 2026-08-07 alone
a running server refused a field added that morning and never saw a guard added that
afternoon — and every real data operation fell back to ad-hoc `python -c`. This is that
fallback given a name, a contract, and tests. The MCP wrapper (`flightdeck.mcp_server`)
spawns this per call, which is what makes the fix reach every surface.

Contract (same family as `scripts/`): ONE JSON document on stdout, nothing else there;
exit 0 on success, 2 when the tool answered `{"error": …}` (a refusal an agent can read
and act on), 3 for an unknown tool, 1 for a crash. Errors are still DATA on stdout —
stderr is reserved for the truly unexpected.

Arguments: `--key value` pairs, each value parsed as JSON when it parses (`--num 13` is
an int, `--confirm true` a bool, `--related '[1,2]'` a list) and kept as a string when
it does not (`--slug flightdeck`). `--json -` reads a JSON object from stdin and is the
right channel for rich prose — Vietnamese markdown with backticks through shell quoting
is exactly the class of accident stdin exists to remove. Explicit `--key` flags override
the JSON body, so a heredoc template can be tweaked without editing it.

There is no session id in the environment (checked: only CLAUDE_CODE_CHILD_SESSION=1),
so attribution is an explicit `session_id` argument — the skills teach it.
"""
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flightdeck.agentsurface import registry, runtime  # noqa: E402

_USAGE = """usage: flightdeck <tool> [--key value ...] [--json -|'{...}']
       flightdeck --list | --schemas | --schema <tool> | --version

JSON on stdout. Exit: 0 ok · 2 tool refused (error in payload) · 3 unknown tool.
Values after --key are parsed as JSON when they parse (13, true, [1,2]) and kept as
strings when they do not. --json - reads an object from stdin; explicit --key flags
override it. See the radar-blips skill for the writing rules the tools enforce."""


def _emit(payload, code: int) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return code


def _coerce(raw: str):
    """`13` → int, `true` → bool, `[1,2]` → list, `flightdeck` → the string itself.

    Bare-word strings failing json.loads IS the design: the common case (slugs, names,
    periods) needs no quoting gymnastics, and anything structured is already JSON.
    """
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _parse(argv: list[str]):
    """(tool, args) from the command line, or an exit code when it handled a meta flag."""
    if not argv or argv[0] in ("--help", "-h"):
        print(_USAGE)
        return None, 0
    if argv[0] == "--version":
        sha = subprocess.run(
            ["git", "-C", str(BACKEND.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True).stdout.strip() or "unknown"
        return None, _emit({"flightdeck": sha, "backend": str(BACKEND)}, 0)
    if argv[0] == "--list":
        for name in registry.merged():
            print(name)
        return None, 0
    if argv[0] == "--schemas":
        return None, _emit(registry.schemas(), 0)
    if argv[0] == "--schema":
        if len(argv) < 2:
            return None, _emit({"error": "--schema needs a tool name"}, 3)
        match = [s for s in registry.schemas() if s["name"] == argv[1]]
        return None, _emit(match[0] if match else {"error": f"unknown tool {argv[1]}"},
                           0 if match else 3)

    tool, rest = argv[0], argv[1:]
    args = {}
    i = 0
    while i < len(rest):
        flag = rest[i]
        if not flag.startswith("--"):
            return None, _emit({"error": f"expected --key, got {flag!r}"}, 1)
        if flag == "--json":
            raw = rest[i + 1] if i + 1 < len(rest) else "-"
            body = sys.stdin.read() if raw == "-" else raw
            try:
                loaded = json.loads(body or "{}")
            except ValueError as e:
                return None, _emit({"error": f"--json is not valid JSON: {e}"}, 1)
            if not isinstance(loaded, dict):
                return None, _emit({"error": "--json must be a JSON object"}, 1)
            # Flags override the body, wherever they appear on the line: a heredoc
            # template plus one tweaked flag is the intended calling style.
            args = {**loaded, **args}
            i += 2
            continue
        if i + 1 >= len(rest):
            return None, _emit({"error": f"{flag} needs a value"}, 1)
        args[flag[2:]] = _coerce(rest[i + 1])
        i += 2
    return (tool, args), None


def main(argv=None) -> int:
    parsed, code = _parse(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        return code
    tool, args = parsed
    tools = registry.merged()
    if tool not in tools:
        return _emit({"error": f"unknown tool {tool} — try `flightdeck --list`"}, 3)
    # Per-invocation lifecycle: no reaper (the process is gone in a second), and the
    # connection is closed explicitly so Postgres never sees a lingering session.
    runtime.configure(reaper=False)
    out = registry.dispatch(tool, args, tools)
    if runtime._state["conn"] is not None:
        try:
            runtime._state["conn"].close()
        except Exception:
            pass
        runtime._state["conn"] = None
    return _emit(out, 2 if isinstance(out, dict) and "error" in out else 0)


if __name__ == "__main__":
    sys.exit(main())
