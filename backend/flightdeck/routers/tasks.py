"""Cost per task and the label review queue.

Reads use a short-lived connection; a label write takes the runtime lock and uses
the shared write connection, matching the rest of the app. Labels themselves are
produced by `flightdeck.tasks` (background loop or CLI).
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from flightdeck import db, tasks
from flightdeck.runtime import since

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class LabelBody(BaseModel):
    kind: str
    parent_id: Optional[str] = None
    merged_ids: List[str] = []
    title: Optional[str] = None


@router.get("")
def list_tasks(request: Request, range: str = "30d", limit: int = 50):
    c = db.open_read(request.app.state.cfg["db_path"])
    try:
        return tasks.rollup(c, since=since(range), limit=max(1, min(limit, 500)))
    finally:
        c.close()


@router.post("/{turn_id}/label")
def label_turn(request: Request, turn_id: str, body: LabelBody):
    if body.kind not in tasks.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(tasks.KINDS)}")
    with request.app.state.runtime.lock:
        ok = tasks.set_label(request.app.state.write_conn, turn_id, body.kind,
                             parent_id=body.parent_id, merged_ids=body.merged_ids,
                             title=body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="turn not found")
    return {"turn_id": turn_id, "kind": body.kind}
