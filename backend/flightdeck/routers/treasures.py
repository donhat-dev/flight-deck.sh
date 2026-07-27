"""Treasures HTTP surface for the dashboard.

Read paths: list, one row, and the rendered artifact bytes. The artifact is
untrusted content, so `/raw` exists to be loaded into an iframe carrying a
bare `sandbox=""` attribute (opaque origin, no scripts) — never to be injected
into the dashboard DOM.

Write paths: `/discover` (scan the transcript tree, optionally import) and
`/source` (save edited markdown/HTML, producing a new version).
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from flightdeck import db
from flightdeck.treasures import filestore, service, store

router = APIRouter(tags=["treasures"])


def _db_path(request: Request) -> str:
    return request.app.state.cfg["db_path"]


@router.get("/api/treasures")
def list_treasures(request: Request, status: str | None = None,
                   language: str | None = None, kind: str | None = None,
                   origin_id: str | None = None, query: str | None = None,
                   limit: int = 200, offset: int = 0):
    with db.read_conn(_db_path(request)) as conn:
        rows = service.list_rows(conn, status=status, language=language,
                                 kind=kind, origin_id=origin_id, query=query,
                                 limit=limit, offset=offset)
    return {"treasures": rows, "count": len(rows)}


@router.get("/api/treasures/{ident}")
def get_treasure(request: Request, ident: str, include_source: bool = False):
    with db.read_conn(_db_path(request)) as conn:
        row = service.get(conn, ident, include_source=include_source)
    if row is None:
        raise HTTPException(status_code=404, detail="treasure not found")
    return row


@router.get("/api/treasures/{ident}/raw")
def raw_treasure(request: Request, ident: str):
    """The rendered artifact, for a sandboxed iframe."""
    with db.read_conn(_db_path(request)) as conn:
        row = service.get(conn, ident)
    if row is None:
        raise HTTPException(status_code=404, detail="treasure not found")
    path = Path(row["artifact_path"]).resolve()
    # The path comes from the index, not the request, but confirm it still sits
    # inside the filestore before reading anything off disk.
    root = filestore.root().resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file missing")
    return Response(content=path.read_text(encoding="utf-8"),
                    media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"})


@router.post("/api/treasures/discover")
def discover_treasures(request: Request, do_import: bool = False,
                       max_files: int = 400, max_age_days: int | None = None):
    """Scan the transcript tree for drafts. POST because `do_import=true`
    writes; the default is a dry run that only reports what it found."""
    from flightdeck.treasures import discover
    cfg = request.app.state.cfg
    conn = db.open_write(cfg["db_path"]) if do_import else db.open_read(cfg["db_path"])
    try:
        return discover.run(conn, cfg["projects_dir"], do_import=do_import,
                            max_files=max_files, max_age_days=max_age_days)
    finally:
        if not do_import:
            conn.close()


class SourceIn(BaseModel):
    content: str


@router.put("/api/treasures/{ident}/source")
def put_source(request: Request, ident: str, body: SourceIn):
    """Save edited markdown/HTML and re-render, producing a NEW version.

    Markdown stays the source of truth: this writes the source and re-runs the
    pandoc pipeline, so the artifact can never drift from its source.
    """
    cfg = request.app.state.cfg
    conn = db.open_write(cfg["db_path"])
    row = service.get(conn, ident)
    if row is None:
        raise HTTPException(status_code=404, detail="treasure not found")
    return service.wrap(conn, title=row["title"], content=body.content,
                        source_format=row["source_format"], kind=row["kind"],
                        language=row["language"], artifact_id=row["id"])




class MetaUpdateIn(BaseModel):
    title: str | None = None
    kind: str | None = None
    language: str | None = None
    status: str | None = None


@router.patch("/api/treasures/{ident}")
def patch_treasure(request: Request, ident: str, body: MetaUpdateIn):
    """Change metadata (title/kind/language/status) without re-rendering.
    Only the given fields are touched; the meta.json sidecar is rewritten so
    disk and the index stay in agreement. Logic lives in service so the MCP
    tool and this endpoint cannot drift apart."""
    conn = db.open_write(request.app.state.cfg["db_path"])
    try:
        return service.update_meta(conn, ident, title=body.title,
                                   kind=body.kind, language=body.language,
                                   status=body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError:
        raise HTTPException(status_code=404, detail="treasure not found")

class LinkSourceIn(BaseModel):
    origin_kind: str | None = None
    origin_id: str | None = None
    origin_path: str | None = None
    published_url: str | None = None
    duplicate_of: str | None = None


@router.post("/api/treasures/{ident}/link")
def link_treasure(request: Request, ident: str, body: LinkSourceIn):
    """Record provenance and/or the published URL. Giving published_url while
    the row is still draft also flips its status to published (shared with the
    MCP tool via service.link_source)."""
    conn = db.open_write(request.app.state.cfg["db_path"])
    try:
        return service.link_source(conn, ident, origin_kind=body.origin_kind,
                                   origin_id=body.origin_id,
                                   origin_path=body.origin_path,
                                   published_url=body.published_url,
                                   duplicate_of=body.duplicate_of)
    except LookupError:
        raise HTTPException(status_code=404, detail="treasure not found")


@router.delete("/api/treasures/{ident}")
def delete_treasure(request: Request, ident: str, confirm: bool = False):
    """PERMANENTLY delete an artifact (files + index row).

    Fail-closed: without `?confirm=true` this refuses, so a stray request can
    never destroy anything. `service.delete` additionally refuses any path that
    is not strictly inside the filestore. To merely hide an artifact, PATCH it
    with status='archived'.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="refused: add ?confirm=true to permanently delete "
                   "(or PATCH status='archived' to hide it instead)")
    conn = db.open_write(request.app.state.cfg["db_path"])
    try:
        return service.delete(conn, ident)
    except LookupError:
        raise HTTPException(status_code=404, detail="treasure not found")
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=f"refused: {e}")
