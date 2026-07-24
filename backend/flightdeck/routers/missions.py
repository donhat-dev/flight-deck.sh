"""Missions endpoints: the kanban board + session-hold state-machine.

Reads use a short-lived connection; every write takes the runtime lock and uses
the shared write connection, matching the rest of the app. The same rows are
read/written out of band by the missions MCP process (see flightdeck.mcp_server).
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from flightdeck import db
from flightdeck.missions import store

router = APIRouter(prefix="/api/missions", tags=["missions"])


class CreateBody(BaseModel):
    title: str
    note: str = ""
    tags: List[str] = []
    priority: str = "NORMAL"
    status: str = "INBOX"
    kind: str = "TODO"
    claim_session: Optional[str] = None


class PatchBody(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None
    tags: Optional[List[str]] = None
    priority: Optional[str] = None
    kind: Optional[str] = None


class ClaimBody(BaseModel):
    session_id: str
    name: Optional[str] = None


def _read(request: Request):
    return db.open_read(request.app.state.cfg["db_path"])


@router.get("")
def list_missions(request: Request):
    c = _read(request)
    try:
        return store.list_missions(c)
    finally:
        c.close()


@router.get("/{mid}")
def get_mission(request: Request, mid: str):
    c = _read(request)
    try:
        m = store.get_mission(c, mid)
    finally:
        c.close()
    if not m:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@router.post("")
def create_mission(request: Request, body: CreateBody):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        return store.create_mission(
            conn, title=body.title, note=body.note, tags=body.tags,
            priority=body.priority, status=body.status, kind=body.kind,
            claim_session=body.claim_session)


@router.patch("/{mid}")
def update_mission(request: Request, mid: str, body: PatchBody):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        m = store.update_mission(conn, mid, **body.dict(exclude_unset=True))
    if not m:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@router.post("/{mid}/claim")
def claim_mission(request: Request, mid: str, body: ClaimBody):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        m = store.claim(conn, mid, body.session_id, name=body.name)
    if not m:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@router.delete("/{mid}")
def delete_mission(request: Request, mid: str):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        ok = store.delete_mission(conn, mid)
    if not ok:
        raise HTTPException(status_code=404, detail="mission not found")
    return {"ok": True, "id": mid}


@router.post("/{mid}/read")
def read_mission(request: Request, mid: str):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        m = store.mark_read(conn, mid)
    if not m:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@router.post("/{mid}/release")
def release_mission(request: Request, mid: str):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        m = store.release(conn, mid)
    if not m:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@router.post("/{mid}/land")
def land_mission(request: Request, mid: str):
    with request.app.state.runtime.lock:
        conn = request.app.state.write_conn
        m = store.land(conn, mid)
    if not m:
        raise HTTPException(status_code=404, detail="mission not found")
    return m
