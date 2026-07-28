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
from flightdeck.treasures import filestore, service, store  # noqa: E402


# claude.ai's Artifact publish cap (see the `Artifact` tool description).
_CLAUDE_AI_ARTIFACT_MAX_BYTES = 16 * 1024 * 1024

# Favicon suggestion by artifact `kind`, for treasure_publish_prepare.
_FAVICON_BY_KIND = {
    "report": "\U0001F4CA",       # 📊
    "spec-review": "\U0001F9ED",  # 🧭
    "note": "\U0001F4DD",         # 📝
    "dataflow": "\U0001F500",     # 🔀
    "deck": "\U0001F39E",         # 🎞
}
_DEFAULT_FAVICON = "\U0001F48E"   # 💎

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
           artifact_id=None, font=None, custom_head=None):
    try:
        return service.wrap(_conn(), title=title, content=content,
                            source_format=source_format, kind=kind,
                            language=language, origin_kind=origin_kind,
                            origin_id=origin_id, origin_path=origin_path,
                            artifact_id=artifact_id, font=font,
                            custom_head=custom_head)
    except ValueError as e:
        return {"error": str(e)}


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


def t_discover(do_import=False, max_files=400, max_age_days=None):
    from flightdeck.treasures import discover
    return discover.run(_conn(), _state["cfg"]["projects_dir"],
                        do_import=do_import, max_files=max_files,
                        max_age_days=max_age_days)


def t_update(ident, title=None, kind=None, language=None, status=None,
             font=None, custom_head=None):
    """Change metadata without re-rendering (shared logic in service)."""
    try:
        return service.update_meta(_conn(), ident, title=title, kind=kind,
                                   language=language, status=status,
                                   font=font, custom_head=custom_head)
    except (LookupError, ValueError) as e:
        return {"error": str(e)}


def t_link_source(ident, origin_kind=None, origin_id=None, origin_path=None,
                  published_url=None, duplicate_of=None):
    """Record provenance / the published URL (shared logic in service)."""
    try:
        return service.link_source(_conn(), ident, origin_kind=origin_kind,
                                   origin_id=origin_id, origin_path=origin_path,
                                   published_url=published_url,
                                   duplicate_of=duplicate_of)
    except LookupError as e:
        return {"error": str(e)}


def _description_from_source(source: str, title: str) -> str:
    """First non-heading line of the markdown source, trimmed to ~160 chars;
    falls back to the title when there is no such line."""
    for line in (source or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) > 160:
            line = line[:157].rstrip() + "..."
        return line
    return title


def t_publish_prepare(ident):
    """Gather everything an agent needs to hand the artifact to the claude.ai
    `Artifact` tool, which is the only way to publish — this tool
    deliberately does not attempt to publish by itself."""
    conn = _conn()
    row = service.get(conn, ident, include_source=True)
    if row is None:
        return {"error": f"not found: {ident}"}
    artifact_path = Path(row["artifact_path"])
    exists = artifact_path.is_file()
    render_bytes = artifact_path.stat().st_size if exists else (row.get("render_bytes") or 0)
    return {
        "artifact_path": str(artifact_path),
        "artifact_exists": exists,
        "title": row["title"],
        "description": _description_from_source(row.get("source") or "", row["title"]),
        "favicon": _FAVICON_BY_KIND.get(row["kind"], _DEFAULT_FAVICON),
        "render_bytes": render_bytes,
        "size_ok": render_bytes <= _CLAUDE_AI_ARTIFACT_MAX_BYTES,
        "next_step": (
            "Publish this file with the Artifact tool "
            f"(file_path={artifact_path}, title={row['title']!r}), then call "
            "treasure_link_source with ident="
            f"{row['id']!r} and published_url set to the URL it returns."),
    }


def t_rerender(ident):
    """Re-render the current version in place — no new version, no content
    change. Use after treasure_update changes font/kind/language/status/
    custom_head, since update_meta alone never touches the HTML on disk."""
    try:
        return service.rerender(_conn(), ident)
    except LookupError as e:
        return {"error": str(e)}


def t_delete(ident, confirm=False):
    """Permanently delete an artifact (files + index row).

    Destructive and fail-closed: without confirm=true nothing happens. Use
    treasure_update(status="archived") to merely hide an artifact.
    """
    if not confirm:
        return {"error": "refused: pass confirm=true to permanently delete "
                         f"{ident!r} (files + index row). To hide it instead, "
                         "use treasure_update with status='archived'."}
    try:
        return service.delete(_conn(), ident)
    except LookupError as e:
        return {"error": str(e)}
    except PermissionError as e:
        return {"error": f"refused: {e}"}


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
         "artifact_id": {"type": "string"},
         "font": {"type": "string",
                  "enum": ["default", "space-grotesk", "jetbrains-mono"],
                  "description": "Body font. Omit to keep the artifact's "
                                 "current font, or 'space-grotesk' for a new one."},
         "custom_head": {"type": "string",
                         "description": "Raw HTML spliced in right before "
                                        "</head> — extra <meta>/<style>/<link> "
                                        "tags. Not escaped; caller-trusted."}},
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
    "treasure_discover": (
        t_discover,
        "Scan ~/.claude/projects for markdown/HTML documents the agent wrote to "
        "files but that are not in the library yet. Dry run by default; pass "
        "do_import=true to wrap and index the new ones. Reports its bounds and "
        "what it skipped.",
        {"do_import": {"type": "boolean"},
         "max_files": {"type": "integer"},
         "max_age_days": {"type": "integer"}},
        []),
    "treasure_update": (
        t_update,
        "Change artifact metadata (title/kind/language/status/font/"
        "custom_head) without re-rendering. Only the given fields are "
        "touched. Rejects an unknown status/font rather than storing it. "
        "font/custom_head only reach the HTML on disk after a follow-up "
        "treasure_rerender (or treasure_wrap with the same artifact_id).",
        {"ident": {"type": "string"},
         "title": {"type": "string"},
         "kind": {"type": "string"},
         "language": {"type": "string", "enum": ["en", "vi"]},
         "status": {"type": "string",
                    "enum": ["draft", "published", "archived"]},
         "font": {"type": "string",
                  "enum": ["default", "space-grotesk", "jetbrains-mono"]},
         "custom_head": {"type": "string"}},
        ["ident"]),
    "treasure_rerender": (
        t_rerender,
        "Re-render the current version in place from its stored source — "
        "no new version, no content change. Use this after treasure_update "
        "changes font/kind/language/status/custom_head to bake it into the "
        "artifact.html on disk.",
        {"ident": {"type": "string"}},
        ["ident"]),
    "treasure_link_source": (
        t_link_source,
        "Record provenance and/or the published URL for an artifact. Giving "
        "published_url while the row is still draft also flips its status "
        "to published — publishing is a state transition on the same row.",
        {"ident": {"type": "string"},
         "origin_kind": {"type": "string"},
         "origin_id": {"type": "string",
                       "description": "Claude session id, when known"},
         "origin_path": {"type": "string"},
         "published_url": {"type": "string"},
         "duplicate_of": {"type": "string"}},
        ["ident"]),
    "treasure_delete": (
        t_delete,
        "PERMANENTLY delete an artifact: its files and its index row. "
        "Fail-closed — nothing happens unless confirm=true is passed. Prefer "
        "treasure_update with status='archived' to just hide something.",
        {"ident": {"type": "string"},
         "confirm": {"type": "boolean",
                     "description": "must be true; guards against accidental loss"}},
        ["ident"]),
    "treasure_publish_prepare": (
        t_publish_prepare,
        "Gather everything needed to hand an artifact to the claude.ai "
        "Artifact tool for publishing — the only way to publish; this tool "
        "does not publish by itself. Returns the artifact path, a "
        "title/description/favicon suggestion, a size verdict against the "
        "16 MiB claude.ai cap, and the next step (publish, then call "
        "treasure_link_source with the returned URL).",
        {"ident": {"type": "string"}},
        ["ident"]),
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
        finally:
            # _conn() is PostgreSQL's WRITE connection (not autocommit) kept
            # open for the server's whole lifetime — a read-only tool call
            # (treasure_get/list) never commits on its own, so without this
            # the session sits "idle in transaction" indefinitely, holding a
            # lock that can block an unrelated ALTER TABLE elsewhere (found
            # the hard way: a stray treasure_get once blocked this exact
            # migration for 14+ hours). Commit after every call, read or write.
            if _state["conn"] is not None:
                _state["conn"].commit()
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
