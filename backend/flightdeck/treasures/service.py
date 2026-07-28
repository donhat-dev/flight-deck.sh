"""The wrap use case: lint -> render -> validate -> write files -> index.

Order matters: the artifact is rendered and written to disk first, and only a
successful render produces an index row. The DB therefore never advertises an
artifact that is not on disk.

The lint and validate stages come from `lint.py` and live HERE rather than in
the callers, so neither the MCP tool nor the dashboard endpoint can bypass them
(docs/treasures-components.md §3).
"""
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.treasures import filestore, lint, render, store


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _render_checked(content, *, source_format, title, language, kind, status,
                    font, custom_head, workdir) -> tuple[str, dict]:
    """Lint -> render -> validate, with the plain-markdown fallback.

    Returns `(content_to_store, rendered)`.

    `content_to_store` carries the lint's blank-line fixes but NEVER the
    fallback's stripped components: deleting an author's components from their
    own source would be data loss, so the strip only ever affects this render's
    HTML. The behaviour stays deterministic — a later rerender of the same
    source reaches the same verdict and strips again.

    Raises lint.ComponentError for an unknown component name (fail-closed:
    called before anything touches disk).
    """
    notes: list[str] = []
    if source_format == "markdown":
        content, fixes = lint.lint(content)
        notes += fixes
    else:
        # An HTML fragment is already markup; the blank-line rule is a markdown
        # concern, so only the allowlist applies.
        bad = lint.unknown_components(content)
        if bad:
            raise lint.ComponentError(
                f"unknown component(s): {', '.join(repr(b) for b in bad)} — "
                f"allowed: {', '.join(lint.COMPONENTS)}")

    def _render(text):
        return render.render(text, source_format=source_format, title=title,
                             language=language, kind=kind, status=status,
                             font=font, custom_head=custom_head,
                             workdir=workdir)

    rendered = _render(content)
    problems = lint.validate(content, rendered["html"])
    if problems:
        # Fallback: ship a readable plain-markdown artifact rather than one
        # printing raw tags as text. The source keeps its components.
        rendered = _render(lint.strip(content))
        notes.append(
            "components stripped from the RENDER (source kept intact) and "
            "re-rendered as plain markdown — " + "; ".join(problems))
    rendered["warnings"] = list(rendered.get("warnings") or []) + notes
    return content, rendered


def _title_from(text: str, source_path: str) -> str:
    """First markdown H1, else the filename stem — so `source_path` alone is
    enough to wrap a document."""
    for line in text.splitlines():
        m = re.match(r"#\s+(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return Path(source_path).stem.replace("-", " ").replace("_", " ").strip()


def wrap(conn, *, title=None, content=None, source_path=None,
         source_format=None, kind="report",
         language="en", origin_kind=None, origin_id=None, origin_path=None,
         authored_at=None, artifact_id=None, font=None, custom_head=None) -> dict:
    """Render a document into a new artifact version and index it.

    Give EITHER `content` (the text) or `source_path` (a file this process
    reads). `source_path` is the better path when the document already exists on
    disk: passing text through the caller means the stored `source_checksum` is
    the hash of whatever arrived, so it can never detect that the text was
    already wrong — verification has to happen on the side that reads the file.

    With `source_path` the title, source_format and origin_path are all derived
    unless given, so `refresh` later needs no arguments at all.

    Pass `artifact_id` to add a version to an existing artifact; omit it to
    create a new one. `font`/`custom_head` default to the artifact's current
    value (or the house default) when omitted, same as `status` below.
    """
    if (content is None) == (source_path is None):
        raise ValueError("pass exactly one of content= or source_path=")
    if source_path is not None:
        # Fail-closed on an out-of-bounds path; see filestore.read_roots().
        content = filestore.read_source(source_path)
        if source_format is None:
            source_format = ("html" if str(source_path).lower()
                             .endswith((".html", ".htm")) else "markdown")
        title = title or _title_from(content, source_path)
        origin_kind = origin_kind or "doc_file"
        # Recording the path is what makes `refresh` argument-free later.
        origin_path = origin_path or str(Path(source_path).expanduser().resolve())
    source_format = source_format or "markdown"
    if not title:
        raise ValueError("title is required when passing content=")

    existing = store.get(conn, artifact_id) if artifact_id else None
    if existing:
        art_id = existing["id"]
        slug = existing["slug"]
        art_dir = Path(existing["dir_path"])
        art_dir.mkdir(parents=True, exist_ok=True)
    else:
        art_id = filestore.new_id()
        slug = filestore.slugify(title)
        # Path only — the directory is created after a successful render, so a
        # render failure cannot leave an empty artifact dir behind.
        art_dir = filestore.artifact_dir_path(slug, art_id)

    version = filestore.next_version(art_dir) if art_dir.exists() else 1
    ext = "md" if source_format == "markdown" else "html"
    # Render in a throwaway dir: pandoc needs its template/CSS/fonts copied
    # beside the source to resolve relative paths, and those build inputs must
    # not accumulate inside every version dir (the rendered HTML already
    # carries them, base64-embedded). When asset support lands, the artifact's
    # assets/ dir gets copied into this workdir before the call.
    status = existing["status"] if existing else "draft"
    if font is not None and font not in VALID_FONT:
        raise ValueError(f"invalid font: {font!r} "
                         f"(expected one of {', '.join(VALID_FONT)})")
    font = font or (existing or {}).get("font") or "space-grotesk"
    custom_head = (custom_head if custom_head is not None
                   else (existing or {}).get("custom_head"))
    with tempfile.TemporaryDirectory(prefix="treasures-render-") as workdir:
        # Lint may rewrite the content (blank-line fixes) or the validator may
        # strip components; `content` is rebound so the source written to disk
        # is exactly what produced this HTML.
        content, rendered = _render_checked(
            content, source_format=source_format, title=title,
            language=language, kind=kind, status=status, font=font,
            custom_head=custom_head, workdir=workdir)
    # Render succeeded — now the artifact dir is worth creating.
    art_dir = filestore.artifact_dir(slug, art_id)
    paths = filestore.write_version(art_dir, version, content, ext,
                                    rendered["html"])

    stamp = now_iso()
    row = {
        "id": art_id,
        "title": title,
        "slug": slug,
        "dir_path": str(art_dir),
        "kind": kind,
        "language": language,
        "status": status,
        "font": font,
        "custom_head": custom_head,
        "version": version,
        "source_format": source_format,
        "source_checksum": filestore.checksum(content),
        "render_checksum": filestore.checksum(rendered["html"]),
        "render_bytes": rendered["bytes"],
        "origin_kind": origin_kind or (existing or {}).get("origin_kind"),
        "origin_id": origin_id or (existing or {}).get("origin_id"),
        "origin_path": origin_path or (existing or {}).get("origin_path"),
        "published_url": (existing or {}).get("published_url"),
        "duplicate_of": (existing or {}).get("duplicate_of"),
        "authored_at": authored_at or (existing or {}).get("authored_at") or stamp,
        "ingested_at": (existing or {}).get("ingested_at") or stamp,
        "updated_at": stamp,
    }
    stored = store.upsert(conn, row)
    filestore.write_meta(art_dir, stored)
    return {**stored,
            "artifact_path": paths["artifact_path"],
            "source_path": paths["source_path"],
            "warnings": rendered["warnings"]}


def _version_paths(row: dict) -> dict:
    vdir = Path(row["dir_path"]) / f"v{row['version']}"
    ext = "md" if row["source_format"] == "markdown" else "html"
    return {"source_path": str(vdir / f"source.{ext}"),
            "artifact_path": str(vdir / "artifact.html")}


def get(conn, ident, *, include_source=False, include_html=False,
        include_stale=False):
    row = store.get(conn, ident)
    if row is None:
        return None
    paths = _version_paths(row)
    out = {**row, **paths, "tags": store.tags_of(conn, row["id"])}
    if include_stale:
        # Derived state, never a column: a stored flag would go wrong the moment
        # the origin changed while this process was down. Only a refreshable
        # origin is hashed, so this costs one file read for 3 of 97 artifacts.
        out["origin_stale"] = stale(conn, ident)
    if include_source:
        out["source"] = Path(paths["source_path"]).read_text(encoding="utf-8")
    if include_html:
        out["html"] = Path(paths["artifact_path"]).read_text(encoding="utf-8")
    return out


def list_rows(conn, **filters):
    """Rows with their file paths and tags attached.

    `_version_paths` is cheap (string joins, no disk access) and without it a
    caller that lists and then wants to open something has to spend a
    `get` per row just to learn where the files are. Tags come from ONE extra
    query for the whole page, never one per row.
    """
    rows = store.list_rows(conn, **filters)
    tags = store.tags_for(conn, [r["id"] for r in rows])
    return [{**row, **_version_paths(row), "tags": tags.get(row["id"], [])}
            for row in rows]


def set_tags(conn, ident, tags):
    """Replace an artifact's tags. Raises LookupError when it is unknown."""
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    return {"id": row["id"], "tags": store.set_tags(conn, row["id"], tags)}


def tag(conn, ident, *, add=None, remove=None):
    """Add and/or remove tags, leaving the rest alone."""
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    if add:
        store.add_tags(conn, row["id"], add)
    if remove:
        store.remove_tags(conn, row["id"], remove)
    return {"id": row["id"], "tags": store.tags_of(conn, row["id"])}


VALID_STATUS = ("draft", "published", "archived")
VALID_FONT = render.FONTS


def _save_meta(conn, row: dict) -> dict:
    """Stamp, upsert, and mirror to the meta.json sidecar in one place, so disk
    and index can never drift apart."""
    row["updated_at"] = now_iso()
    stored = store.upsert(conn, row)
    filestore.write_meta(Path(stored["dir_path"]), stored)
    return stored


def update_meta(conn, ident, *, title=None, kind=None, language=None,
                status=None, font=None, custom_head=None) -> dict:
    """Change metadata without re-rendering. Only the given fields move.

    Raises LookupError when the artifact is unknown and ValueError on an
    unknown status/font, so each caller can map those to its own error shape.
    Like kind/language/status, font/custom_head only reach the rendered HTML
    on the next `wrap` (same artifact_id) or `rerender` call — this call alone
    changes the index/meta.json, not the artifact.html already on disk.
    """
    if status is not None and status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status!r} "
                         f"(expected one of {', '.join(VALID_STATUS)})")
    if font is not None and font not in VALID_FONT:
        raise ValueError(f"invalid font: {font!r} "
                         f"(expected one of {', '.join(VALID_FONT)})")
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    for field, val in (("title", title), ("kind", kind),
                       ("language", language), ("status", status),
                       ("font", font), ("custom_head", custom_head)):
        if val is not None:
            row[field] = val
    return _save_meta(conn, row)


def link_source(conn, ident, *, origin_kind=None, origin_id=None,
                origin_path=None, published_url=None, duplicate_of=None) -> dict:
    """Record provenance / the published URL.

    Publishing is a state transition on the same row (never a second entity):
    supplying `published_url` while the row is still a draft also flips its
    status to `published`.
    """
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    for field, val in (("origin_kind", origin_kind), ("origin_id", origin_id),
                       ("origin_path", origin_path),
                       ("duplicate_of", duplicate_of)):
        if val is not None:
            row[field] = val
    if published_url is not None:
        row["published_url"] = published_url
        if row["status"] == "draft":
            row["status"] = "published"
    return _save_meta(conn, row)


def delete(conn, ident) -> dict:
    """Permanently remove an artifact: its files, then its index row.

    Destructive, so it is fail-closed. `dir_path` comes from the index, but a
    corrupted or hand-edited row must never be able to point this at, say, a
    home directory: the resolved path has to sit strictly INSIDE the filestore,
    and it may not be the filestore root itself. Anything else raises
    PermissionError and nothing at all is removed — not even the row.

    Prefer `update_meta(status="archived")` when the artifact should merely
    leave the active list.
    """
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")

    root = filestore.root().resolve()
    target = Path(row["dir_path"]).resolve()
    if target == root or root not in target.parents:
        raise PermissionError(
            f"refusing to delete {target}: outside the filestore ({root})")

    removed_files = 0
    if target.is_dir():
        for f in sorted((p for p in target.rglob("*") if p.is_file()),
                        reverse=True):
            f.unlink()
            removed_files += 1
        for d in sorted((p for p in target.rglob("*") if p.is_dir()),
                        reverse=True):
            d.rmdir()
        target.rmdir()
    # Files are gone; drop the row last so a failure above leaves the index
    # pointing at something a retry can still find. Tags go explicitly because
    # SQLite does not enforce the FK's ON DELETE CASCADE.
    store.delete_tags(conn, row["id"])
    store.delete(conn, row["id"])
    return {"deleted": row["id"], "title": row["title"],
            "dir_path": str(target), "removed_files": removed_files}


REFRESHABLE_SUFFIXES = (".md", ".html", ".htm")


def _refreshable(row: dict) -> tuple[bool, str]:
    """Can this artifact's origin be re-read as a document?

    Decided by the PATH, not by `origin_kind`. Measured on the live library: an
    agent had wrapped a real `Subscription_Service/18-….md` while labelling it
    `origin_kind="claude_session"` (true — it came from a session — but the path
    is a document). Gating on the label refused a genuinely refreshable artifact,
    while gating on the path handles every case correctly:
      - the 91 `.jsonl` transcripts are not documents and grow every turn
      - a URL origin is not a local file
      - a real .md/.html is refreshable whatever the label says
    """
    path = row.get("origin_path")
    if not path:
        return False, "no origin_path recorded"
    if not str(path).lower().endswith(REFRESHABLE_SUFFIXES):
        return False, (f"origin_path is not a document file "
                       f"({'/'.join(REFRESHABLE_SUFFIXES)}): {path}")
    return True, ""


def refresh(conn, ident, *, force=False) -> dict:
    """Re-read the artifact's own `origin_path` and store it as a NEW version.

    The complement to `rerender`, which re-runs the pipeline over the source
    already stored — useful after a template change, useless while the document
    itself is still being edited. This is the "the file moved on, catch up" verb,
    and it needs no path argument because `origin_path` was recorded at wrap
    time.

    Only an origin that is a real document file qualifies — see `_refreshable`.

    Conditional by default: when the origin hashes the same as the stored source
    there is nothing to record, so it reports `skipped` instead of minting an
    identical version. Pass `force=True` to version it regardless.
    """
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    ok, why = _refreshable(row)
    if not ok:
        raise ValueError(
            f"{ident} cannot be refreshed: {why}. Wrap it again with "
            f"source_path= to point it at a document.")
    if not force:
        current = filestore.checksum(filestore.read_source(row["origin_path"]))
        if current == row["source_checksum"]:
            return {**get(conn, row["id"]), "skipped": True,
                    "reason": "origin matches the stored source; nothing to "
                              "version. Pass force=true to version it anyway."}
    return wrap(conn, source_path=row["origin_path"], artifact_id=row["id"],
                kind=row["kind"], language=row["language"])


def stale(conn, ident) -> dict:
    """Has the origin document changed since the stored source was written?

    Answerable only for a refreshable origin, for the reason in `refresh`.
    Returns the verdict plus both checksums so the caller can see why.
    """
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    ok, why = _refreshable(row)
    if not ok:
        return {"id": row["id"], "refreshable": False, "reason": why,
                "stale": None}
    origin_path = row["origin_path"]
    try:
        current = filestore.checksum(filestore.read_source(origin_path))
    except (OSError, PermissionError) as e:
        return {"id": row["id"], "refreshable": True, "stale": None,
                "reason": f"origin unreadable: {e}", "origin_path": origin_path}
    return {"id": row["id"], "refreshable": True,
            "stale": current != row["source_checksum"],
            "origin_path": origin_path,
            "stored_checksum": row["source_checksum"],
            "origin_checksum": current}


def rerender(conn, ident) -> dict:
    """Re-render the current version IN PLACE from its stored source.

    Used after the template or tokens change: the source did not change, so
    bumping the version would add an empty entry to the history. Only the
    rendered bytes, their checksum and `updated_at` move.
    """
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    paths = _version_paths(row)
    source = Path(paths["source_path"]).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="treasures-rerender-") as workdir:
        # Same gate as wrap, so an artifact re-rendered after a template change
        # cannot end up held to a weaker contract than a freshly wrapped one.
        fixed, rendered = _render_checked(
            source, source_format=row["source_format"], title=row["title"],
            language=row["language"], kind=row["kind"], status=row["status"],
            font=row.get("font") or "space-grotesk",
            custom_head=row.get("custom_head"), workdir=workdir)
    if fixed != source:
        # A lint autofix is semantically neutral (blank lines only), so persist
        # it in place rather than leaving the stored source subtly broken for
        # every future reader. No version bump: the document did not change.
        Path(paths["source_path"]).write_text(fixed, encoding="utf-8")
        row["source_checksum"] = filestore.checksum(fixed)
    Path(paths["artifact_path"]).write_text(rendered["html"], encoding="utf-8")
    row["render_checksum"] = filestore.checksum(rendered["html"])
    row["render_bytes"] = rendered["bytes"]
    row["updated_at"] = now_iso()
    stored = store.upsert(conn, row)
    filestore.write_meta(Path(row["dir_path"]), stored)
    return {**stored, **paths, "warnings": rendered["warnings"]}


def rerender_all(conn, **filters) -> dict:
    """Re-render every indexed artifact. One failure never stops the batch —
    the caller gets the list of what could not be re-rendered."""
    done, failed = 0, []
    for row in store.list_rows(conn, limit=100000, **filters):
        try:
            rerender(conn, row["id"])
            done += 1
        except Exception as e:
            failed.append({"id": row["id"], "title": row["title"],
                           "error": f"{type(e).__name__}: {e}"[:300]})
    return {"rerendered": done, "failed": failed}
