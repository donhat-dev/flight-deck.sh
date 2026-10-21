"""Approval channel: the HTTP surface a blocked tool call waits on.

Registered by `server.create_app()` from the start so that the wiring is one
concern and the endpoints are another — this module is the endpoints only, and
the pending-decision store plus the escalation policy live in
`flightdeck.decisions`.

Contract:

    POST /api/decisions                a hook escalates a pending tool call
    GET  /api/decisions                open decisions, for the dock to render
    POST /api/decisions/{id}/resolve   a human answers approve/reject/edit
    GET  /api/decisions/{id}           the hook's read-back, for its blocking wait

Live events ride `events.BUS` under the `decision.*` kinds, so the dock updates
without polling; the hook polls this router because a shell script cannot hold
an SSE connection.

Read/write split mirrors `routers/missions.py`: GETs use a short-lived
`db.open_read` connection, writes take `runtime.lock` and use the shared
`app.state.write_conn` — the same convention as every other write endpoint in
this app, so one PreToolUse hook writing at the same moment `ingest` is
flushing does not race the same connection.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from flightdeck import db, decisions as store

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class CreateBody(BaseModel):
    tool_name: str
    command: str
    session_id: Optional[str] = None
    pattern: Optional[str] = None
    cwd: Optional[str] = None


class ResolveBody(BaseModel):
    action: str
    edited_command: Optional[str] = None
    note: Optional[str] = None


def _read(request: Request):
    return db.open_read(request.app.state.cfg["db_path"])


@router.post("")
def create_decision(request: Request, body: CreateBody):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        return store.create(
            conn, tool_name=body.tool_name, command=body.command,
            session_id=body.session_id, pattern=body.pattern, cwd=body.cwd)


@router.get("")
def list_decisions(request: Request):
    conn = _read(request)
    try:
        return {"decisions": store.list_pending(conn)}
    finally:
        conn.close()


@router.get("/{did}")
def get_decision(request: Request, did: str):
    conn = _read(request)
    try:
        row = store.get(conn, did)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return row


@router.post("/{did}/resolve")
def resolve_decision(request: Request, did: str, body: ResolveBody):
    if body.action not in ("approve", "reject", "edit"):
        raise HTTPException(
            status_code=400,
            detail="action must be one of approve, reject, edit")
    if body.action == "edit" and not body.edited_command:
        raise HTTPException(
            status_code=400, detail="edit requires edited_command")
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        try:
            row = store.resolve(
                conn, did, action=body.action,
                edited_command=body.edited_command, note=body.note)
        except store.AlreadyResolvedError:
            raise HTTPException(
                status_code=409, detail="decision already resolved")
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return row
