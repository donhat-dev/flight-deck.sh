"""The wrap use case: render -> write files -> index.

Order matters: the artifact is rendered and written to disk first, and only a
successful render produces an index row. The DB therefore never advertises an
artifact that is not on disk.
"""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.treasures import filestore, render, store


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wrap(conn, *, title, content, source_format="markdown", kind="report",
         language="en", origin_kind=None, origin_id=None, origin_path=None,
         authored_at=None, artifact_id=None) -> dict:
    """Render `content` into a new artifact version and index it.

    Pass `artifact_id` to add a version to an existing artifact; omit it to
    create a new one.
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
    with tempfile.TemporaryDirectory(prefix="treasures-render-") as workdir:
        rendered = render.render(content, source_format=source_format,
                                 title=title, language=language, kind=kind,
                                 workdir=workdir)
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
        "status": existing["status"] if existing else "draft",
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


def _save_meta(conn, row: dict) -> dict:
    """Stamp, upsert, and mirror to the meta.json sidecar in one place, so disk
    and index can never drift apart."""
    row["updated_at"] = now_iso()
    stored = store.upsert(conn, row)
    filestore.write_meta(Path(stored["dir_path"]), stored)
    return stored


def update_meta(conn, ident, *, title=None, kind=None, language=None,
                status=None) -> dict:
    """Change metadata without re-rendering. Only the given fields move.

    Raises LookupError when the artifact is unknown and ValueError on an
    unknown status, so each caller can map those to its own error shape.
    """
    if status is not None and status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status!r} "
                         f"(expected one of {', '.join(VALID_STATUS)})")
    row = store.get(conn, ident)
    if row is None:
        raise LookupError(f"not found: {ident}")
    for field, val in (("title", title), ("kind", kind),
                       ("language", language), ("status", status)):
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
        rendered = render.render(source, source_format=row["source_format"],
                                 title=row["title"], language=row["language"],
                                 kind=row["kind"], workdir=workdir)
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
