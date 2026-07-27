#!/usr/bin/env python3
"""Treasures MCP server — stdio, newline-delimited JSON-RPC.

Three tools in slice 1: wrap content into a self-contained artifact, read one
back, and list the library. The agent supplies content only; this server owns
format, style, and render.

It runs outside FlightDeck's process (any session can call it), so it resolves
its own configuration: the repo-root `.env` for TOKEN_AUDIT_DATABASE_URL, then
config.toml through flightdeck.config. `handle()` is a pure function of a
request dict, which is what the tests exercise.
"""
import json
import os
import sys
from pathlib import Path

# Allow `python .../treasures/mcp_server.py` from any cwd: backend/ is two
# levels up from this file, and that is what makes `flightdeck` importable.
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flightdeck import config, db                       # noqa: E402
from flightdeck.treasures import service, store         # noqa: E402

_state = {"cfg": None, "conn": None}


def _load_dotenv() -> None:
    """systemd injects .env for the web app; an MCP server gets no such help,
    so read the repo-root .env ourselves (KEY=VALUE lines, no export/quotes)."""
    env_path = BACKEND.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def configure(cfg: dict | None = None) -> None:
    """Wire the engine + schema once. Tests pass an explicit cfg."""
    if cfg is None:
        _load_dotenv()
        cfg = config.load(os.environ.get(
            "TOKEN_AUDIT_CONFIG", str(BACKEND / "config.toml")))
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    store.init(conn)
    _state["cfg"] = cfg
    _state["conn"] = conn


def _conn():
    if _state["conn"] is None:
        configure()
    return _state["conn"]


def t_wrap(title, content, source_format="markdown", kind="report",
           language="en", origin_kind=None, origin_id=None, origin_path=None,
           artifact_id=None):
    return service.wrap(_conn(), title=title, content=content,
                        source_format=source_format, kind=kind,
                        language=language, origin_kind=origin_kind,
                        origin_id=origin_id, origin_path=origin_path,
                        artifact_id=artifact_id)


def t_get(ident, include_source=False, include_html=False):
    row = service.get(_conn(), ident, include_source=include_source,
                      include_html=include_html)
    return row if row is not None else {"error": f"not found: {ident}"}


def t_list(status=None, language=None, kind=None, origin_id=None, query=None,
           limit=100, offset=0):
    rows = service.list_rows(_conn(), status=status, language=language,
                             kind=kind, origin_id=origin_id, query=query,
                             limit=limit, offset=offset)
    return {"treasures": rows, "count": len(rows)}


TOOLS = {
    "treasure_wrap": (
        t_wrap,
        "Wrap markdown (or an HTML fragment) into a self-contained, "
        "publish-ready HTML artifact, store it, and index it. Returns the "
        "artifact row plus artifact_path and any warnings. Pass artifact_id to "
        "add a version to an existing artifact.",
        {"title": {"type": "string"},
         "content": {"type": "string"},
         "source_format": {"type": "string", "enum": ["markdown", "html"]},
         "kind": {"type": "string"},
         "language": {"type": "string", "enum": ["en", "vi"]},
         "origin_kind": {"type": "string"},
         "origin_id": {"type": "string",
                       "description": "Claude session id, when known"},
         "origin_path": {"type": "string"},
         "artifact_id": {"type": "string"}},
        ["title", "content"]),
    "treasure_get": (
        t_get,
        "Read one artifact by id or slug, optionally with its markdown source "
        "and/or rendered HTML.",
        {"ident": {"type": "string"},
         "include_source": {"type": "boolean"},
         "include_html": {"type": "boolean"}},
        ["ident"]),
    "treasure_list": (
        t_list,
        "List stored artifacts, newest first, with optional filters.",
        {"status": {"type": "string"},
         "language": {"type": "string"},
         "kind": {"type": "string"},
         "origin_id": {"type": "string"},
         "query": {"type": "string"},
         "limit": {"type": "integer"},
         "offset": {"type": "integer"}},
        []),
}


def handle(req: dict):
    """Map one JSON-RPC request to a response dict (None for notifications)."""
    mid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "treasures", "version": "0.1"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r}}
            for n, (_f, d, p, r) in TOOLS.items()]}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        entry = TOOLS.get(name)
        try:
            out = entry[0](**args) if entry else {"error": f"unknown tool {name}"}
        except Exception as e:                      # surface as data, never crash
            out = {"error": f"{type(e).__name__}: {e}"}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}}
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
