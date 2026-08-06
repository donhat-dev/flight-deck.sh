"""Radar storage — event-sourced, because a radar without its history is a picture.

The load-bearing decision is that a blip has NO ring column. Its ring is the ring of
its newest move, derived on read. Storing the position as well would create two
truths that drift the first time a move is inserted out of order, and the whole
value of the radar is that "where does this stand" and "why does it stand there" can
never disagree.

The second decision is that a move without a reason and at least one piece of
evidence is not representable. `add_move` refuses it. A radar of positions with no
reasons is a wall decoration; the refusal is what keeps it a record.

Schema notes:
  - `blips.num` is unique per radar, not globally. It is the number a reader sees on
    the drawing, so it belongs to the radar's own numbering.
  - moves carry `session_id`, so a move can be traced back to the session that made
    it — the same squawk the rest of FlightDeck records.
  - evidence hangs off the MOVE, not the blip. Evidence is what justified a
    particular move; re-parenting it to the blip would lose which decision it
    supported.
"""
from datetime import datetime, timezone

from flightdeck import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS radar (
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  subtitle TEXT,
  jira TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS radar_blip (
  id TEXT PRIMARY KEY,
  radar TEXT NOT NULL,
  num INTEGER NOT NULL,
  name TEXT NOT NULL,
  quadrant TEXT NOT NULL,
  created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_radar_blip_num ON radar_blip(radar, num);
CREATE INDEX IF NOT EXISTS idx_radar_blip_radar ON radar_blip(radar);

CREATE TABLE IF NOT EXISTS radar_move (
  id TEXT PRIMARY KEY,
  blip TEXT NOT NULL,
  ring TEXT,
  period TEXT NOT NULL,
  why TEXT NOT NULL,
  ts TEXT NOT NULL,
  session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_radar_move_blip ON radar_move(blip);
CREATE INDEX IF NOT EXISTS idx_radar_move_ts ON radar_move(ts);

CREATE TABLE IF NOT EXISTS radar_evidence (
  id TEXT PRIMARY KEY,
  move TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  ref TEXT,
  dated TEXT
);
CREATE INDEX IF NOT EXISTS idx_radar_evidence_move ON radar_evidence(move);
"""

RINGS = ("adopt", "trial", "assess", "caution")
QUADRANTS = ("platforms", "techniques", "tools", "lang")


def init(conn) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    import secrets
    return f"{prefix}_{secrets.token_hex(6)}"


# --- radars -------------------------------------------------------------------

def upsert_radar(conn, *, slug, title, subtitle=None, jira=None) -> dict:
    stamp = now_iso()
    conn.execute(
        "INSERT INTO radar (slug, title, subtitle, jira, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (slug) DO UPDATE SET title = excluded.title, "
        "subtitle = excluded.subtitle, jira = excluded.jira, updated_at = excluded.updated_at",
        (slug, title, subtitle, jira, stamp, stamp))
    conn.commit()
    return get_radar(conn, slug)


def get_radar(conn, slug) -> dict | None:
    row = conn.execute(
        "SELECT slug, title, subtitle, jira, created_at, updated_at FROM radar "
        "WHERE slug = ?", (slug,)).fetchone()
    return dict(zip(("slug", "title", "subtitle", "jira", "created_at", "updated_at"), row)) if row else None


def list_radars(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT slug, title, subtitle, jira, created_at, updated_at FROM radar "
        "ORDER BY updated_at DESC").fetchall()
    cols = ("slug", "title", "subtitle", "jira", "created_at", "updated_at")
    return [dict(zip(cols, r)) for r in rows]


# --- blips --------------------------------------------------------------------

def add_blip(conn, *, radar, num, name, quadrant) -> dict:
    if quadrant not in QUADRANTS:
        raise ValueError(f"unknown quadrant {quadrant!r} — one of {', '.join(QUADRANTS)}")
    bid = new_id("blip")
    conn.execute(
        "INSERT INTO radar_blip (id, radar, num, name, quadrant, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (bid, radar, int(num), name, quadrant, now_iso()))
    conn.commit()
    return {"id": bid, "radar": radar, "num": int(num), "name": name, "quadrant": quadrant}


def blips_of(conn, radar) -> list[dict]:
    rows = conn.execute(
        "SELECT id, radar, num, name, quadrant FROM radar_blip WHERE radar = ? "
        "ORDER BY num", (radar,)).fetchall()
    cols = ("id", "radar", "num", "name", "quadrant")
    return [dict(zip(cols, r)) for r in rows]


def blip_by_num(conn, radar, num) -> dict | None:
    row = conn.execute(
        "SELECT id, radar, num, name, quadrant FROM radar_blip "
        "WHERE radar = ? AND num = ?", (radar, int(num))).fetchone()
    return dict(zip(("id", "radar", "num", "name", "quadrant"), row)) if row else None


# --- moves and evidence -------------------------------------------------------

def add_move(conn, *, blip, ring, period, why, evidence, session_id=None, ts=None) -> dict:
    """Record a move. Refuses one that cannot justify itself.

    `ring=None` means "entered the radar" — the first move, which has a reason but no
    position yet. Every other move must name a real ring.
    """
    if not (why or "").strip():
        raise ValueError("a move needs a reason: `why` is required")
    if ring is not None and ring not in RINGS:
        raise ValueError(f"unknown ring {ring!r} — one of {', '.join(RINGS)}")
    # The entry move is the one exception: nothing has been decided yet, so there is
    # nothing to cite. Every later move is a decision and has to show its evidence.
    if ring is not None and not evidence:
        raise ValueError("a move needs at least one piece of evidence")
    mid = new_id("move")
    conn.execute(
        "INSERT INTO radar_move (id, blip, ring, period, why, ts, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mid, blip, ring, period, why.strip(), ts or now_iso(), session_id))
    for e in evidence or []:
        conn.execute(
            "INSERT INTO radar_evidence (id, move, kind, title, ref, dated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("ev"), mid, e.get("kind") or "note", e["title"],
             e.get("ref"), e.get("dated")))
    conn.commit()
    return {"id": mid, "blip": blip, "ring": ring, "period": period, "why": why.strip()}


def moves_of(conn, blip) -> list[dict]:
    """Newest first — the order the panel reads them in."""
    rows = conn.execute(
        "SELECT id, ring, period, why, ts, session_id FROM radar_move "
        "WHERE blip = ? ORDER BY ts DESC", (blip,)).fetchall()
    cols = ("id", "ring", "period", "why", "ts", "session_id")
    return [dict(zip(cols, r)) for r in rows]


def evidence_of(conn, move_ids) -> dict[str, list[dict]]:
    """Evidence for many moves in ONE query, keyed by move id.

    Batched deliberately: a per-move query turns one blip into a dozen round trips,
    and the panel always wants the whole history at once.
    """
    ids = list(move_ids)
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT move, kind, title, ref, dated FROM radar_evidence "
        f"WHERE move IN ({marks}) ORDER BY dated DESC", tuple(ids)).fetchall()
    out: dict[str, list[dict]] = {}
    for move, kind, title, ref, dated in rows:
        out.setdefault(move, []).append(
            {"kind": kind, "title": title, "ref": ref, "dated": dated})
    return out


def all_moves_of_radar(conn, radar) -> list[dict]:
    """Every move on a radar, newest first, with its blip. One query for the whole
    board, so the ring of every blip can be derived without N+1."""
    rows = conn.execute(
        "SELECT m.id, m.blip, m.ring, m.period, m.why, m.ts "
        "FROM radar_move m JOIN radar_blip b ON b.id = m.blip "
        "WHERE b.radar = ? ORDER BY m.ts DESC", (radar,)).fetchall()
    cols = ("id", "blip", "ring", "period", "why", "ts")
    return [dict(zip(cols, r)) for r in rows]
