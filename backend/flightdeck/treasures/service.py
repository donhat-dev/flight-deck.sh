"""The wrap use case: lint -> render -> validate -> write files -> index.

Order matters: the artifact is rendered and written to disk first, and only a
successful render produces an index row. The DB therefore never advertises an
artifact that is not on disk.

The lint and validate stages come from `lint.py` and live HERE rather than in
the callers, so neither the MCP tool nor the dashboard endpoint can bypass them
(docs/treasures-components.md §3).
"""
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


def wrap(conn, *, title, content, source_format="markdown", kind="report",
         language="en", origin_kind=None, origin_id=None, origin_path=None,
         authored_at=None, artifact_id=None, font=None, custom_head=None) -> dict:
    """Render `content` into a new artifact version and index it.

    Pass `artifact_id` to add a version to an existing artifact; omit it to
    create a new one. `font`/`custom_head` default to the artifact's current
    value (or the house default) when omitted, same as `status` below.
    """
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


def get(conn, ident, *, include_source=False, include_html=False):
    row = store.get(conn, ident)
    if row is None:
        return None
    paths = _version_paths(row)
    out = {**row, **paths}
    if include_source:
        out["source"] = Path(paths["source_path"]).read_text(encoding="utf-8")
    if include_html:
        out["html"] = Path(paths["artifact_path"]).read_text(encoding="utf-8")
    return out


def list_rows(conn, **filters):
    return store.list_rows(conn, **filters)


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
    # pointing at something a retry can still find.
    store.delete(conn, row["id"])
    return {"deleted": row["id"], "title": row["title"],
            "dir_path": str(target), "removed_files": removed_files}


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
