"""Quota burn rate: tick-to-tick spans of a /usage meter with ledger figures."""
from typing import Optional

from fastapi import APIRouter, Request

from flightdeck import burn, db
from flightdeck.runtime import since

router = APIRouter(prefix="/api/burn", tags=["burn"])


@router.get("")
def burn_spans(request: Request, meter: str = "session", range: str = "7d",
               min_points: Optional[float] = None):
    c = db.open_read(request.app.state.cfg["db_path"])
    try:
        return burn.spans(c, meter=meter, since=since(range), min_points=min_points)
    finally:
        c.close()


@router.get("/stats")
def burn_stats(request: Request, range: str = "7d", window: int = 3600):
    c = db.open_read(request.app.state.cfg["db_path"])
    try:
        return burn.stats(c, since=since(range), window_s=max(300, min(window, 86400)))
    finally:
        c.close()
