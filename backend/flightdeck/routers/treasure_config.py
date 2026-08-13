"""Site-wide Treasures defaults: HTTP surface.

GET/PUT for the three site-wide defaults (default_agent_notes,
default_header_html, default_footer_html) that `treasures.render` splices
into every artifact rendered afterwards — NOT `custom_head`, which already
exists, is PER-ARTIFACT, and splices into that one artifact's <head>. These
three are house-wide and land in the visible <body> (see
treasures/store.py's CONFIG_KEYS and treasures/render.py's
inject_body_defaults).

Deliberately mounted at /api/treasure-config, NOT under /api/treasures: the
treasures router (flightdeck.routers.treasures) is a coworker's in-flight
file and may hold a path like /api/treasures/{ident} that would shadow a
literal /api/treasures/config depending on registration order. A separate
prefix cannot be shadowed by anything that router does, and cannot be broken
by their edits.
"""
from fastapi import APIRouter, Body, HTTPException, Request

from flightdeck import db
from flightdeck.treasures import service

router = APIRouter(prefix="/api/treasure-config", tags=["treasures"])


def _db_path(request: Request) -> str:
    return request.app.state.cfg["db_path"]


@router.get("")
def get_config(request: Request):
    conn = db.open_read(_db_path(request))
    try:
        return service.config_get(conn)
    finally:
        conn.close()


@router.put("")
def put_config(request: Request, body: dict = Body(...)):
    """Body is a JSON object with any subset of the three config keys.

    A plain dict, not a pydantic model with typed fields: an unknown key and a
    non-string value both need to come back as a 400 with a specific message,
    where a typed model would make FastAPI answer both with an opaque 422
    before this code ever runs.
    """
    for key, val in body.items():
        if not isinstance(val, str):
            raise HTTPException(
                400, f"value for {key!r} must be a string, got "
                     f"{type(val).__name__}")
    conn = db.open_write(_db_path(request))
    try:
        return service.config_set(conn, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()
