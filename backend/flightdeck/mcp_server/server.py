"""FastMCP server exposing the Missions board to agent sessions.

It shares the FlightDeck web backend's store by loading the SAME config and
engine (db.configure): PostgreSQL when TOKEN_AUDIT_DATABASE_URL is set (the live
service), else the SQLite file at cfg["db_path"]. Because it hits the same store
via db.py, a `mission_claim` here shows up in the web kanban within the UI's 2s
poll -- regardless of engine. Reuses flightdeck.missions.store so the hold
state-machine is identical to the REST layer.

Run from the backend/ dir (config.toml + env are read from there):
    ../.venv/bin/python -m flightdeck.mcp_server
"""
import os
from typing import List, Optional

from fastmcp import FastMCP

from flightdeck import config, db
from flightdeck.missions import store

_cfg = config.load(os.environ.get("TOKEN_AUDIT_CONFIG", "config.toml"))
db.configure(_cfg)  # PostgreSQL if TOKEN_AUDIT_DATABASE_URL set, else SQLite
_DB = _cfg["db_path"]

# Ensure the missions tables exist even if the web backend has not started yet
# (idempotent; on the shared store this is a no-op once the service has run).
_boot = db.open_write(_DB)
try:
    store.init(_boot)
finally:
    _boot.close()

mcp = FastMCP("missions")


def _conn():
    # A short-lived write-capable connection per tool call; store functions
    # commit and we close (returning it to the pool on PostgreSQL).
    return db.open_write(_DB)


def _notfound(mid: str) -> dict:
    return {"error": "mission not found", "id": mid}


@mcp.tool()
def missions_list(status: Optional[str] = None, kind: Optional[str] = None) -> dict:
    """List missions + live holding sessions. Each mission has a `kind`:
    NOTE = memory/reference to READ, TODO = task to ACT ON. Filter with
    kind="NOTE" (read the memory) or kind="TODO" (see the tasks); optional
    status filter (INBOX | TODO | IN_FLIGHT | DONE)."""
    c = _conn()
    try:
        data = store.list_missions(c)
    finally:
        c.close()
    if status:
        data["missions"] = [m for m in data["missions"] if m["status"] == status]
    if kind:
        data["missions"] = [m for m in data["missions"] if m["kind"] == kind]
    return data


@mcp.tool()
def mission_get(id: str) -> dict:
    """Get one mission by id, including its session-hold log."""
    c = _conn()
    try:
        m = store.get_mission(c, id)
    finally:
        c.close()
    return m or _notfound(id)


@mcp.tool()
def mission_create(title: str, note: str = "", tags: Optional[List[str]] = None,
                   priority: str = "NORMAL", status: str = "INBOX",
                   kind: str = "TODO", claim_session: Optional[str] = None) -> dict:
    """Create a mission. kind="NOTE" for memory/reference, "TODO" for a task
    (default). Pass claim_session to also hold it for that session on create."""
    c = _conn()
    try:
        return store.create_mission(c, title=title, note=note, tags=tags or [],
                                    priority=priority, status=status, kind=kind,
                                    claim_session=claim_session)
    finally:
        c.close()


@mcp.tool()
def mission_update(id: str, title: Optional[str] = None, note: Optional[str] = None,
                   status: Optional[str] = None, priority: Optional[str] = None,
                   tags: Optional[List[str]] = None, kind: Optional[str] = None) -> dict:
    """Update a mission's content or status; only the fields you pass change.
    There is no rich editor yet, so an agent marks a TODO done by editing its note
    (e.g. "note abc" -> "note abc (done)") and/or setting status="DONE". Editing
    while you hold it keeps the hold fresh (stays ACTIVE)."""
    fields = {k: v for k, v in {"title": title, "note": note, "status": status,
                                "priority": priority, "tags": tags, "kind": kind}.items()
              if v is not None}
    c = _conn()
    try:
        m = store.update_mission(c, id, **fields)
    finally:
        c.close()
    return m or _notfound(id)


@mcp.tool()
def mission_claim(id: str, session_id: str, name: Optional[str] = None) -> dict:
    """Take a hold on a mission for session_id, with an optional display name
    (overwrites any existing hold). Prefer mission_claim_as if you want a friendly
    identity instead of the raw session id."""
    c = _conn()
    try:
        m = store.claim(c, id, session_id, name=name)
    finally:
        c.close()
    return m or _notfound(id)


@mcp.tool()
def mission_claim_as(id: str, session_id: str) -> dict:
    """Take a hold under a RANDOM friendly identity (e.g. "Icarus Quill") instead of
    the raw session hash. Returns the mission; its hold.name is the assigned identity
    to reuse (as `name`) on later mission_claim / mission_heartbeat calls."""
    c = _conn()
    try:
        m = store.claim(c, id, session_id, name=store.random_name())
    finally:
        c.close()
    return m or _notfound(id)


@mcp.tool()
def mission_release(id: str) -> dict:
    """Release the current hold on a mission."""
    c = _conn()
    try:
        m = store.release(c, id)
    finally:
        c.close()
    return m or _notfound(id)


@mcp.tool()
def mission_land(id: str) -> dict:
    """Mark a mission DONE and clear its hold."""
    c = _conn()
    try:
        m = store.land(c, id)
    finally:
        c.close()
    return m or _notfound(id)


@mcp.tool()
def mission_delete(id: str) -> dict:
    """Permanently delete a mission and its history."""
    c = _conn()
    try:
        ok = store.delete_mission(c, id)
    finally:
        c.close()
    return {"ok": True, "id": id} if ok else _notfound(id)


@mcp.tool()
def sessions_list() -> list:
    """List the sessions currently holding a mission."""
    c = _conn()
    try:
        return store.list_sessions(c)
    finally:
        c.close()
