"""The tool registry and its transport-neutral dispatch.

Every domain keeps its own `TOOLS` table — `{name: (fn, description, schema_props,
required)}` — where the tool logic lives. This module is what turns those tables into a
surface: `merged()` collects them, `dispatch()` runs one call with the error-as-data and
commit-after-every-call rules, and `handle()` frames it as JSON-RPC for whichever stdio
server asks. The CLI and the MCP wrapper are both thin clients of these three functions,
which is the consolidation: one execution path, two frontends.

Domain modules are imported LAZILY, inside `merged()`. At module import time the domains
import this module (for `handle`), so a top-level import back at them would be circular;
lazily, the registry also never pays for a domain the call does not touch.
"""
import json

from flightdeck.agentsurface import runtime

# Name-prefix → module holding that prefix's TOOLS. A new domain is one line here.
_DOMAINS = {
    "radar_": "flightdeck.radar.mcp_server",
    "treasure_": "flightdeck.treasures.mcp_server",
}


def merged() -> dict:
    """Every domain's TOOLS in one table, refusing silent shadowing."""
    import importlib
    out = {}
    for prefix, module_name in _DOMAINS.items():
        tools = importlib.import_module(module_name).TOOLS
        for name in tools:
            if name in out:
                raise RuntimeError(f"tool name {name!r} defined by two domains")
        out.update(tools)
    return out


def schemas(tools: dict | None = None) -> list[dict]:
    tools = merged() if tools is None else tools
    return [{"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r}}
            for n, (_f, d, p, r) in tools.items()]


def dispatch(name: str, args: dict, tools: dict | None = None) -> dict:
    """Run one tool call. Errors come back as data, never as a crash — one bad call
    must not end an agent's session, on any transport."""
    tools = merged() if tools is None else tools
    entry = tools.get(name)
    try:
        return entry[0](**(args or {})) if entry else {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        runtime.commit_quietly()


def handle(req: dict, tools: dict | None = None, server: str = "flightdeck"):
    """One JSON-RPC request → one response dict (None for notifications).

    `tools`/`server` let the per-domain compatibility servers keep their old scoped
    behaviour — a session that spawned the old `radar` entry still sees radar tools
    under the radar name — while the flightdeck surface serves everything.
    """
    mid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": server, "version": "0.2"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": schemas(tools)}}
    if method == "tools/call":
        out = dispatch(params.get("name"), params.get("arguments") or {}, tools)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method {method}"}}
    return None


def serve_stdio(tools: dict | None = None, server: str = "flightdeck") -> None:
    """The newline-delimited stdio loop every server variant shares."""
    import sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req, tools, server)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
