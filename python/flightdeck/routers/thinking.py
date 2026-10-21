"""Thinking time and tokens per API response, per day (flightdeck.thinking)."""
from typing import Optional

from fastapi import APIRouter, Request

from flightdeck import db, thinking
from flightdeck.runtime import since

router = APIRouter(prefix="/api/thinking", tags=["thinking"])


@router.get("")
def thinking_series(request: Request, range: str = "30d", model: Optional[str] = None,
                    effort: Optional[str] = None, agent: str = "all"):
    c = db.open_read(request.app.state.cfg["db_path"])
    try:
        return thinking.series(c, since=since(range), model=model or None,
                               effort=effort or None, agent=agent)
    finally:
        c.close()
