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
