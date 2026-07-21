"""Session endpoints: the paginated session list + per-session transcript,
Route Loom projection, and clearance-route lanes.

The session list is served from the cached snapshot for the default page; other
pages and the per-session projections are computed on demand from the raw JSONL
(the ledger stores usage rows, not content).
"""
from fastapi import APIRouter, HTTPException, Request

from flightdeck import db, metrics, route, transcript
from flightdeck.runtime import cached, since

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
def sessions(request: Request, limit: int = 100, offset: int = 0,
             range: str = "all"):
    if limit == 100 and offset == 0:
        hit = cached(request.app, range, "sessions")
        if hit is not None:
            return hit
    cfg = request.app.state.cfg
    c = db.open_read(cfg["db_path"])
    try:
        return metrics.sessions(c, limit, offset, since=since(range),
                                projects_dir=cfg["projects_dir"])
    finally:
        c.close()


@router.get("/api/session/{session_id}")
def session_detail(request: Request, session_id: str, offset: int = 0,
                   limit: int = 4000):
    # Read-only chat transcript for one session, parsed on demand from the
    # raw JSONL (the SQLite ledger stores only usage rows, not content).
    data = transcript.load_session(
        request.app.state.cfg["projects_dir"], session_id,
        offset=offset, limit=limit)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@router.get("/api/session/{session_id}/route")
def session_route(request: Request, session_id: str, max_waypoints: int = 24):
    # Deterministic, streaming projection: large transcripts become a
    # bounded Route Loom overview without loading every turn into memory.
    data = route.load_session_route(
        request.app.state.cfg["projects_dir"], session_id,
        max_waypoints=max_waypoints)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@router.get("/api/session/{session_id}/clearance-routes")
def clearance_routes(request: Request, session_id: str, max_units: int = 9):
    # One deterministic role-lane route PER user instruction: the turns a
    # single instruction triggered, grouped into units from its start to
    # the agent's closing summary.
    data = route.load_clearance_routes(
        request.app.state.cfg["projects_dir"], session_id, max_units=max_units)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data
