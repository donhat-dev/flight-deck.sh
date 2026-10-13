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
                   limit: int = 4000, anchor: str = "head",
                   subagent_turns: bool = False):
    # Read-only chat transcript for one session, parsed on demand from the
    # raw JSONL (the SQLite ledger stores only usage rows, not content).
    #
    # `anchor=tail` returns the LAST `limit` turns. A 38k-turn session read from
    # the head shows its opening and can never reach its end; the reader wants
    # the newest turns first and pages backwards from there.
    if anchor not in ("head", "tail"):
        raise HTTPException(status_code=400, detail="anchor must be head or tail")
    data = transcript.load_session(
        request.app.state.cfg["projects_dir"], session_id,
        offset=offset, limit=limit, anchor=anchor,
        subagent_turns=subagent_turns)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@router.get("/api/session/{session_id}/subagent/{agent_id}")
def session_subagent(request: Request, session_id: str, agent_id: str):
    # One nested thread, fetched when the reader expands it. The session
    # payload carries only its metadata.
    data = transcript.load_subagent(
        request.app.state.cfg["projects_dir"], session_id, agent_id)
    if data is None:
        raise HTTPException(status_code=404, detail="subagent not found")
    return data


@router.get("/api/session/{session_id}/usage")
def session_usage(request: Request, session_id: str):
    # The four readings the transcript itself cannot answer: tokens, cost, and
    # the turn/model spread. They live in the ledger, which aggregates per
    # session anyway - this endpoint just picks one row out of that aggregate
    # instead of making the reader load the whole logbook to see them.
    cfg = request.app.state.cfg
    c = db.open_read(cfg["db_path"])
    try:
        for row in metrics.sessions(c, limit=1_000_000, offset=0,
                                    projects_dir=cfg["projects_dir"]):
            if row["session_id"] == session_id:
                return row
    finally:
        c.close()
    raise HTTPException(status_code=404, detail="session not in the ledger")


@router.get("/api/session/{session_id}/tools")
def session_tools(request: Request, session_id: str, limit: int = 20):
    # The shape of the work, for the whole session rather than the window the
    # reader happens to have loaded. `tool_calls` is written at ingest, one row
    # per tool_use block, so this is a group-by rather than a file scan.
    cfg = request.app.state.cfg
    c = db.open_read(cfg["db_path"])
    try:
        rows = c.execute(
            "SELECT tool, server, COUNT(*) AS n FROM tool_calls "
            "WHERE session_id = ? GROUP BY tool, server ORDER BY n DESC",
            (session_id,)).fetchall()
    finally:
        c.close()
    items = [{"tool": r["tool"], "server": r["server"], "count": r["n"]} for r in rows]
    total = sum(i["count"] for i in items)
    by_server: dict[str, int] = {}
    for i in items:
        by_server[i["server"] or "built-in"] = by_server.get(i["server"] or "built-in", 0) + i["count"]
    head = items[:max(1, limit)]
    tail = sum(i["count"] for i in items[max(1, limit):])
    return {
        "session_id": session_id,
        "total": total,
        "distinct": len(items),
        "tools": head,
        "other": tail,
        "by_server": sorted(({"server": k, "count": v} for k, v in by_server.items()),
                            key=lambda x: -x["count"]),
    }


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
