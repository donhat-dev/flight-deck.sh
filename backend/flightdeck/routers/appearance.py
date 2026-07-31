"""Appearance config — which face and weight each kind of text uses.

Stored server-side rather than in localStorage, which is the difference between a
page for *trying* fonts and a real config page: the choice belongs to the FlightDeck
install, so it survives a cleared browser profile and applies to any client that
opens the deck.

The font catalogue itself deliberately stays in the frontend (`ui/appearance.js`) —
which faces exist and which weights they really carry is a rendering fact, and
duplicating that table in Python would guarantee the two drift. So this validates
SHAPE, not vocabulary: the three known roles, a non-empty font id, and a weight in
the CSS range. A frontend test checks the catalogue's weights against `fonts.css`,
which is where an offered-but-unavailable weight would actually come from.
"""
import json

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import db

router = APIRouter()

KEY = "appearance"


class RoleChoice(BaseModel):
    font: str = Field(min_length=1, max_length=64)
    weight: int = Field(ge=100, le=900)


class Appearance(BaseModel):
    primary: RoleChoice
    label: RoleChoice
    mono: RoleChoice


@router.get("/api/appearance")
def read_appearance(request: Request):
    """The saved config, or null when nothing has been saved — the client owns the
    defaults because it owns the catalogue."""
    conn = db.open_read(request.app.state.cfg["db_path"])
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"appearance": None}
    try:
        return {"appearance": json.loads(row[0])}
    except (TypeError, ValueError):
        # A corrupt row must not wedge the page: the client falls back to defaults
        # rather than taking a 500 on every load.
        return {"appearance": None}


@router.put("/api/appearance")
def write_appearance(body: Appearance, request: Request):
    conn = db.open_write(request.app.state.cfg["db_path"])
    payload = json.dumps(body.model_dump(), separators=(",", ":"))
    try:
        # Both engines accept this ON CONFLICT form, and `?` is translated to `%s`
        # for PostgreSQL by the connection wrapper.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (KEY, payload))
        conn.commit()
    finally:
        conn.close()
    return {"appearance": body.model_dump()}
