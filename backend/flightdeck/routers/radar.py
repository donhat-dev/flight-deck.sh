"""Radar API.

Read paths return the DERIVED board — ring, state and evidence age resolved — because
the drawing needs those and the client must not re-implement the derivation. The
write path is one endpoint, `POST .../moves`, and it is the only way a blip's
position changes: there is no "set ring" call, because a position without the move
that produced it is exactly what this feature exists to prevent.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from flightdeck import db
from flightdeck.radar import service, store

router = APIRouter(prefix="/api/radar")


class Evidence(BaseModel):
    kind: str = Field(default="note", max_length=32)
    title: str = Field(min_length=1, max_length=300)
    ref: str | None = Field(default=None, max_length=500)
    dated: str | None = Field(default=None, max_length=40)


class Move(BaseModel):
    ring: str = Field(min_length=1, max_length=16)
    period: str = Field(min_length=1, max_length=32)
    why: str = Field(min_length=1, max_length=2000)
    # Optional, and empty by default. It used to be `min_length=1` with no default, so a
    # caller with nothing to cite got a 422 — which bought citations that existed to
    # satisfy the check rather than to support the move. The REASON is still required
    # above; evidence is recommended at the surfaces that ask a human for it.
    evidence: list[Evidence] = Field(default_factory=list)
    session_id: str | None = Field(default=None, max_length=64)


def _read(request):
    return db.open_read(request.app.state.cfg["db_path"])


@router.get("/radars")
def list_radars(request: Request):
    conn = _read(request)
    try:
        return {"radars": [service.radar_board(conn, r["slug"]) for r in store.list_radars(conn)]}
    finally:
        conn.close()


@router.get("/{slug}")
def get_radar(slug: str, request: Request):
    conn = _read(request)
    try:
        board = service.radar_board(conn, slug)
    finally:
        conn.close()
    if board is None:
        raise HTTPException(404, f"no radar {slug!r}")
    return board


@router.get("/{slug}/blips/{num}")
def get_blip(slug: str, num: int, request: Request):
    conn = _read(request)
    try:
        detail = service.blip_detail(conn, slug, num)
    finally:
        conn.close()
    if detail is None:
        raise HTTPException(404, f"no blip {num} on radar {slug!r}")
    return detail


@router.post("/{slug}/blips/{num}/moves")
def move_blip(slug: str, num: int, body: Move, request: Request):
    conn = db.open_write(request.app.state.cfg["db_path"])
    try:
        return service.move_blip(
            conn, slug, num, ring=body.ring, period=body.period, why=body.why,
            evidence=[e.model_dump() for e in body.evidence],
            session_id=body.session_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        # The service's own words: "a move needs at least one piece of evidence" is
        # something a form can show, where a bare 400 is not.
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()
