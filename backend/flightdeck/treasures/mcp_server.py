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
import threading
import time
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

_state = {"cfg": None, "conn": None, "used_at": 0.0}

# An MCP server lives as long as its Claude Code session, which can be days, and
# it used to hold its write connection for that whole time. Measured on a machine
# with ~12 open sessions that was 12 idle PostgreSQL connections doing nothing —
# not a leak (every process had a live parent) but a cost that grows with every
# session and never shrinks.
#
# So the connection is now released after a period of inactivity and reopened on
# the next call. A parked session settles at ZERO connections. The reaper has to
# run on a timer rather than lazily: a parked process never calls again, which is
# exactly the case worth reclaiming.
_IDLE_TTL = float(os.environ.get("TREASURES_CONN_IDLE_TTL", "180"))
_REAP_EVERY = 20.0
_lock = threading.RLock()


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
    # Schema work needs a connection but not a lasting one, so this opens, migrates
    # and closes. The serving connection is opened lazily by _conn().
    conn = db.open_write(cfg["db_path"])
    try:
        store.init(conn)
    finally:
        conn.close()
    with _lock:
        _state["cfg"] = cfg
        _state["conn"] = None
        _state["used_at"] = 0.0
    _start_reaper()


def _conn():
    """The write connection, opened on demand and kept only while in use."""
    with _lock:
        if _state["cfg"] is None:
            configure()
        if _state["conn"] is None:
            _state["conn"] = db.open_write(_state["cfg"]["db_path"])
        _state["used_at"] = time.monotonic()
        return _state["conn"]


def release_idle(ttl: float = None) -> bool:
    """Close the write connection if it has been unused for `ttl` seconds.

    Returns True when a connection was actually closed, so a test can assert the
    reclaim happened rather than assuming the timer fired.
    """
    limit = _IDLE_TTL if ttl is None else ttl
    with _lock:
        conn = _state["conn"]
        if conn is None or time.monotonic() - _state["used_at"] < limit:
            return False
        _state["conn"] = None
        try:
            conn.close()
        except Exception:
            # A connection the server can no longer close is already useless; the
            # next call opens a fresh one, so this must not take the server down.
            pass
        return True


def _start_reaper() -> None:
    if _state.get("reaper"):
        return

    def loop():
        while True:
            time.sleep(_REAP_EVERY)
            try:
                release_idle()
            except Exception:
                pass

    thread = threading.Thread(target=loop, name="treasures-conn-reaper", daemon=True)
    _state["reaper"] = thread
    thread.start()


def t_wrap(title=None, content=None, source_path=None, source_format=None,
           kind="report", language="en", origin_kind=None, origin_id=None,
           origin_path=None, artifact_id=None, font=None, custom_head=None):
    try:
        return service.wrap(_conn(), title=title, content=content,
                            source_path=source_path,
                            source_format=source_format, kind=kind,
                            language=language, origin_kind=origin_kind,
                            origin_id=origin_id, origin_path=origin_path,
                            artifact_id=artifact_id, font=font,
                            custom_head=custom_head)
    except ValueError as e:
        # Covers an invalid font AND lint.ComponentError (a ValueError
        # subclass) — an unknown component name refuses the wrap outright.
        return {"error": str(e)}
    except (OSError, PermissionError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def t_refresh(ident, force=False):
    """Re-read the artifact's own origin file into a new version."""
    try:
        return service.refresh(_conn(), ident, force=force)
    except LookupError as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": str(e)}
    except (OSError, PermissionError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def t_stale(ident):
    try:
        return service.stale(_conn(), ident)
    except LookupError as e:
        return {"error": str(e)}


def t_get(ident, include_source=False, include_html=False):
    row = service.get(_conn(), ident, include_source=include_source,
                      include_html=include_html)
    return row if row is not None else {"error": f"not found: {ident}"}


def t_list(status=None, language=None, kind=None, origin_id=None, query=None,
           origin_root=None, tag=None, limit=100, offset=0):
    rows = service.list_rows(_conn(), status=status, language=language,
                             kind=kind, origin_id=origin_id, query=query,
                             origin_root=origin_root, tag=tag,
                             limit=limit, offset=offset)
    return {"treasures": rows, "count": len(rows)}


def t_tag(ident, add=None, remove=None, set=None):
    """Add/remove tags, or replace the whole set."""
    try:
        if set is not None:
            return service.set_tags(_conn(), ident, set)
        return service.tag(_conn(), ident, add=add, remove=remove)
    except LookupError as e:
        return {"error": str(e)}


def t_tags():
    from flightdeck.treasures import store as _store
    return {"tags": _store.all_tags(_conn())}


def t_discover(do_import=False, max_files=400, max_age_days=None, roots=None):
    from flightdeck.treasures import discover
    return discover.run(_conn(), _state["cfg"]["projects_dir"],
                        do_import=do_import, max_files=max_files,
                        max_age_days=max_age_days, roots=roots)


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
    """Export a publish-ready FRAGMENT and return what the claude.ai `Artifact`
    tool needs. Publishing itself stays with the agent — this tool deliberately
    does not attempt it.

    `file_path` points at a freshly exported `fragment.html`, not at the
    standalone `artifact.html`. The Artifact tool supplies the doctype, <head>
    and <body> itself, so handing it the full document lost three things at
    once: the `<body class>` every typography rule hangs off, the page
    background (painted on <html>), and the colour mode. The fragment moves all
    three onto a wrapper we own, which is what makes a hand-edit unnecessary.

    `document_path` is still reported, for opening or sending the file locally.
    """
    conn = _conn()
    row = service.get(conn, ident)
    if row is None:
        return {"error": f"not found: {ident}"}
    try:
        frag = service.export_fragment(conn, row["id"])
    except (LookupError, ValueError, RuntimeError, OSError) as e:
        return {"error": f"fragment export failed: {e}"}
    render_bytes = frag["render_bytes"]
    return {
        "file_path": frag["fragment_path"],
        "fragment_path": frag["fragment_path"],
        "document_path": row["artifact_path"],
        "document_exists": Path(row["artifact_path"]).is_file(),
        "title": row["title"],
        "description": _description_from_source(frag["source"], row["title"]),
        "favicon": _FAVICON_BY_KIND.get(row["kind"], _DEFAULT_FAVICON),
        "render_bytes": render_bytes,
        "size_ok": render_bytes <= _CLAUDE_AI_ARTIFACT_MAX_BYTES,
        "warnings": frag["warnings"],
        "next_step": (
            "Publish this file AS IS with the Artifact tool "
            f"(file_path={frag['fragment_path']}, title={row['title']!r}) — it is "
            "already body-only and self-contained, so do not edit it. Then call "
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
        "add a version to an existing artifact.\n"
        "PREFER source_path when the document is already a file: this server "
        "reads it, so the stored checksum is of the real bytes and no drift is "
        "possible. Passing content instead means the checksum only ever proves "
        "what arrived, not that it was right. With source_path the title, "
        "source_format and origin_path are all derived, and treasure_refresh "
        "then works with no arguments.",
        {"title": {"type": "string",
                   "description": "required with content=; derived from the "
                                  "first H1 or the filename with source_path="},
         "content": {"type": "string",
                     "description": "the document text; mutually exclusive "
                                    "with source_path"},
         "source_path": {"type": "string",
                         "description": "path this server reads instead. Must "
                                        "be inside the allowed source roots "
                                        "(workspace, ~/.claude/projects, "
                                        "filestore) or it is refused."},
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
        # Nothing is unconditionally required: content= needs title, while
        # source_path= derives it. service.wrap enforces the pairing.
        []),
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
        "List stored artifacts, newest first, with optional filters. Rows carry "
        "source_path, artifact_path and tags, so a list->open flow needs no "
        "follow-up treasure_get.",
        {"status": {"type": "string"},
         "language": {"type": "string"},
         "kind": {"type": "string"},
         "origin_id": {"type": "string"},
         "query": {"type": "string",
                   "description": "substring of the title or slug"},
         "origin_root": {"type": "string",
                         "description": "prefix of origin_path — one call for "
                                        "'everything that came out of this "
                                        "folder', or from a URL prefix"},
         "tag": {"type": "string", "description": "only artifacts with this tag"},
         "limit": {"type": "integer"},
         "offset": {"type": "integer"}},
        []),
    "treasure_tag": (
        t_tag,
        "Add and/or remove an artifact's tags, or replace the whole set with "
        "set=[...]. Tags are normalised to lowercase and deduped, so filtering "
        "by tag is an exact match. Returns the resulting tag list.",
        {"ident": {"type": "string"},
         "add": {"type": "array", "items": {"type": "string"}},
         "remove": {"type": "array", "items": {"type": "string"}},
         "set": {"type": "array", "items": {"type": "string"},
                 "description": "replace every tag with this list"}},
        ["ident"]),
    "treasure_tags": (
        t_tags,
        "Every tag in use with its artifact count, most-used first.",
        {},
        []),
    "treasure_discover": (
        t_discover,
        "Find documents that are not in the library yet. By default it mines "
        "~/.claude/projects transcripts. Pass roots=[...] to ALSO walk real "
        ".md/.html files in those directories — the only way to pick up a "
        "document written straight to the workspace, which the transcript scan "
        "cannot see. Dry run by default; pass do_import=true to wrap and index "
        "the new ones. Reports its bounds, what it skipped, and any root it "
        "refused as outside the allowed source roots.",
        {"do_import": {"type": "boolean"},
         "max_files": {"type": "integer"},
         "max_age_days": {"type": "integer"},
         "roots": {"type": "array", "items": {"type": "string"},
                   "description": "extra directories to walk for real document "
                                  "files, e.g. a workspace subfolder"}},
        []),
    "treasure_refresh": (
        t_refresh,
        "Re-read an artifact's own origin file and store it as a NEW version — "
        "the 'the document moved on, catch up' verb. Needs no path: origin_path "
        "was recorded at wrap time. Use this while a document is still being "
        "edited; use treasure_rerender instead when only the template changed. "
        "Only artifacts wrapped from a real file qualify (a transcript origin is "
        "provenance, not a re-readable source).\n"
        "Conditional: when the origin already hashes the same as the stored "
        "source it reports skipped=true rather than minting an identical "
        "version. Pass force=true to version it regardless.",
        {"ident": {"type": "string"},
         "force": {"type": "boolean",
                   "description": "version it even when the origin is unchanged"}},
        ["ident"]),
    "treasure_stale": (
        t_stale,
        "Has the origin document changed since the stored source was written? "
        "Compares the stored source_checksum against a fresh hash of "
        "origin_path. Answers 'refreshable: false' for a transcript origin, "
        "which cannot be re-read.",
        {"ident": {"type": "string"}},
        ["ident"]),
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
