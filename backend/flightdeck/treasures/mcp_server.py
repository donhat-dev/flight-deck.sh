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
import sys
from pathlib import Path

# Allow `python .../treasures/mcp_server.py` from any cwd: backend/ is two
# levels up from this file, and that is what makes `flightdeck` importable.
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flightdeck.agentsurface import registry, runtime        # noqa: E402
from flightdeck.treasures import filestore, service, store   # noqa: E402


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

# The shared runtime, under the names this module always exported. Tests reach through
# these (`_state["conn"] = Spy()`, `release_idle(ttl=0)`), and keeping them is what let
# the consolidation land without rewriting this suite.
configure = runtime.configure
release_idle = runtime.release_idle
_state = runtime._state
_conn = runtime.conn


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
        "agent_notes_path": frag["agent_notes_path"],
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
            f"{row['id']!r} and published_url set to the URL it returns. The "
            "published fragment carries only generic identity (no tags, no "
            f"origin, no time context) — {frag['agent_notes_path']} is where "
            "that context lives, read-only for you, never published."),
    }


def t_config_get():
    """Read the three site-wide defaults."""
    return service.config_get(_conn())


def t_config_set(default_agent_notes=None, default_header_html=None,
                 default_footer_html=None):
    """Set any subset of the three site-wide defaults. Fail-closed with an
    error payload (never raise) when nothing was passed at all."""
    values = {k: v for k, v in {
        "default_agent_notes": default_agent_notes,
        "default_header_html": default_header_html,
        "default_footer_html": default_footer_html,
    }.items() if v is not None}
    if not values:
        return {"error": "pass at least one of default_agent_notes, "
                         "default_header_html, default_footer_html"}
    return service.config_set(_conn(), values)


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
    "treasure_config_get": (
        t_config_get,
        "Read the three site-wide Treasures defaults (default_agent_notes, "
        "default_header_html, default_footer_html) — house content applied "
        "to every artifact the pipeline renders. Always returns all three "
        "keys (\"\" when unset) plus updated_at.",
        {},
        []),
    "treasure_config_set": (
        t_config_set,
        "Set any subset of the three site-wide Treasures defaults "
        "(default_agent_notes, default_header_html, default_footer_html). "
        "These apply to EVERY artifact the pipeline generates AFTER this "
        "call — an artifact already on disk only picks up the new value "
        "once you run treasure_rerender on it. At least one argument is "
        "required; calling with none returns an error payload.",
        {"default_agent_notes": {
             "type": "string",
             "description": "markdown TEXT (not HTML) — the house note for "
                             "agents reading a published artifact. Rendered "
                             "escaped inside a collapsed <details>, never "
                             "passed through pandoc."},
         "default_header_html": {
             "type": "string",
             "description": "raw HTML placed at the top of the document "
                             "body, right after <main class=\"doc\">."},
         "default_footer_html": {
             "type": "string",
             "description": "raw HTML placed at the bottom of the document "
                             "body, right before </main>."}},
        []),
    "treasure_publish_prepare": (
        t_publish_prepare,
        "Gather everything needed to hand an artifact to the claude.ai "
        "Artifact tool for publishing — the only way to publish; this tool "
        "does not publish by itself. Returns the artifact path, a "
        "title/description/favicon suggestion, a size verdict against the "
        "16 MiB claude.ai cap, agent_notes_path (the local file carrying tags/"
        "origin/provenance that the published fragment deliberately omits), "
        "and the next step (publish, then call treasure_link_source with the "
        "returned URL).",
        {"ident": {"type": "string"}},
        ["ident"]),
}


def handle(req: dict):
    """The scoped compatibility server: treasure tools only, under the old name."""
    return registry.handle(req, TOOLS, server="treasures")


def main() -> None:
    registry.serve_stdio(TOOLS, server="treasures")


if __name__ == "__main__":
    main()
