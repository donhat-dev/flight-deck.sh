"""Tickets endpoints: the dev-side board and the plan tree behind each ticket.

Reads use a short-lived connection; every write takes the runtime lock and the
shared write connection, matching the rest of the app.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from flightdeck import db
from flightdeck.tickets import store

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class TicketPatch(BaseModel):
    title: Optional[str] = None
    lane: Optional[str] = None
    baton: Optional[str] = None
    jira_status: Optional[str] = None
    track: Optional[str] = None
    sprint: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    estimate: Optional[str] = None
    parent_key: Optional[str] = None
    frd_url: Optional[str] = None
    spec_url: Optional[str] = None
    worktree: Optional[str] = None
    branch: Optional[str] = None
    mr_url: Optional[str] = None
    blocked_reason: Optional[str] = None
    tags: Optional[List[str]] = None


class CreateBody(BaseModel):
    key: str
    title: str
    lane: str = "IMPLEMENTATION"
    phase: str = "OPEN"
    baton: str = "MINE"
    jira_status: str = "Open"
    track: str = ""
    sprint: str = ""
    priority: str = "Normal"
    assignee: str = ""
    estimate: str = ""
    parent_key: Optional[str] = None
    frd_url: str = ""
    spec_url: str = ""
    worktree: str = ""
    branch: str = ""
    mr_url: str = ""
    blocked_reason: str = ""
    tags: List[str] = []


class PhaseBody(BaseModel):
    phase: str


class StepBody(BaseModel):
    title: str
    parent_id: Optional[str] = None
    code: str = ""
    body: str = ""
    status: str = "planned"
    kind: str = ""
    estimate_h: float = 0
    origin: str = ""


class StepPatch(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    status: Optional[str] = None
    kind: Optional[str] = None
    estimate_h: Optional[float] = None
    origin: Optional[str] = None
    code: Optional[str] = None
    position: Optional[int] = None


def _read(request: Request):
    return db.open_read(request.app.state.cfg["db_path"])


def _write(request: Request):
    return request.app.state.write_conn


@router.get("")
def list_tickets(request: Request):
    c = _read(request)
    try:
        return store.list_tickets(c)
    finally:
        c.close()


@router.post("")
def create_ticket(request: Request, body: CreateBody):
    fields = body.dict()
    key = fields.pop("key").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    fields["tags"] = json.dumps(fields.get("tags") or [])
    if fields.get("blocked_reason"):
        fields["blocked_since"] = store._now()
    with request.app.state.runtime.lock:
        conn = _write(request)
        if store.get_ticket(conn, key):
            raise HTTPException(status_code=409, detail=f"{key} already exists")
        return store.create_ticket(conn, key, fields.pop("title"), **fields)


@router.get("/{key}")
def get_ticket(request: Request, key: str):
    c = _read(request)
    try:
        t = store.get_ticket(c, key)
    finally:
        c.close()
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    return t


@router.patch("/{key}")
def patch_ticket(request: Request, key: str, body: TicketPatch):
    with request.app.state.runtime.lock:
        t = store.update_ticket(_write(request), key, **body.dict(exclude_unset=True))
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    return t


@router.post("/{key}/phase")
def move_phase(request: Request, key: str, body: PhaseBody):
    with request.app.state.runtime.lock:
        t, reason = store.move_phase(_write(request), key, body.phase)
    if t is None:
        raise HTTPException(status_code=404 if reason is None else 400,
                            detail=reason or "ticket not found")
    if reason:
        # The gate is the answer, not an error to retry: 409 carries the reason
        # the board shows in place of moving the card.
        raise HTTPException(status_code=409, detail=reason)
    return t


@router.post("/{key}/steps")
def create_step(request: Request, key: str, body: StepBody):
    with request.app.state.runtime.lock:
        conn = _write(request)
        if not store.get_ticket(conn, key):
            raise HTTPException(status_code=404, detail="ticket not found")
        return store.create_step(conn, key, **body.dict())


@router.patch("/steps/{sid}")
def patch_step(request: Request, sid: str, body: StepPatch):
    with request.app.state.runtime.lock:
        s = store.update_step(_write(request), sid, **body.dict(exclude_unset=True))
    if not s:
        raise HTTPException(status_code=404, detail="step not found")
    return s


@router.delete("/steps/{sid}")
def delete_step(request: Request, sid: str):
    with request.app.state.runtime.lock:
        ok = store.delete_step(_write(request), sid)
    if not ok:
        raise HTTPException(status_code=404, detail="step not found")
    return {"ok": True, "id": sid}
