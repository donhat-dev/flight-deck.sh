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
  created_at TEXT,
  description TEXT,
  ref TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_radar_blip_num ON radar_blip(radar, num);
CREATE INDEX IF NOT EXISTS idx_radar_blip_radar ON radar_blip(radar);

-- Related blips, stored ONE row per stated relation and read in BOTH directions.
-- "A is related to B" is not a claim about direction, so storing both rows would make
-- every relation two facts that can disagree, and asking the caller to state it twice
-- would make half of them get stated once.
CREATE TABLE IF NOT EXISTS radar_blip_link (
  blip TEXT NOT NULL,
  related TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_radar_blip_link ON radar_blip_link(blip, related);
CREATE INDEX IF NOT EXISTS idx_radar_blip_link_rev ON radar_blip_link(related);

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

# Distinguishes "leave this field alone" from "set it to null". Without it a partial
# update cannot clear a nullable column, and `subtitle=None` would be ambiguous
# between the two — the caller's intent has to survive the call signature.
_KEEP = object()
# Public alias: `service` shares this exact object. A second sentinel would compare
# unequal by identity, so every partial update would read as "field passed".
KEEP = _KEEP


BLIP_COLS = ("id", "radar", "num", "name", "quadrant", "description", "ref")
_BLIP_SELECT = ", ".join(BLIP_COLS)


def init(conn) -> None:
    conn.executescript(_SCHEMA)
    # `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a
    # column added to the schema above never reaches an install that predates it.
    for column in ("description", "ref"):
        _ensure_column(conn, "radar_blip", column)
    conn.commit()


def _ensure_column(conn, table, column) -> None:
    """Add a TEXT column if it is missing. One helper rather than a function per
    column, because the only thing that ever varies is the two names."""
    if db.is_postgres():
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} text")
        return
    # SQLite has no IF NOT EXISTS on ADD COLUMN, so the check has to be explicit.
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


def now_iso() -> str:
    """Microseconds, not seconds, because `ts` is what ORDERS the history.

    A blip's ring is the ring of its newest move, and "newest" is decided by `ts DESC`.
    At second precision two moves recorded in the same second carry the SAME timestamp,
    the sort between them is undefined, and the derived ring becomes arbitrary — the one
    thing this store promises cannot happen. A person never records two moves in one
    second; an agent calling the MCP does it routinely, which is how this surfaced.

    Mixing precisions is safe in the only direction that occurs. Second-precision rows
    already stored end in `+00:00`, microsecond rows in `.123456+00:00`, and `.` (0x2E)
    sorts after `+` (0x2B) — so a new move always sorts after an older one inside the
    same second. `test_mixed_precision_timestamps_still_order_correctly` pins that
    rather than leaving it as a lucky property of ASCII.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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


def update_radar(conn, slug, *, title=_KEEP, subtitle=_KEEP, jira=_KEEP) -> dict:
    """Change a radar's labels. Its slug is its identity and is not editable here:
    renaming it would orphan every URL and every blip row that points at it."""
    cur = get_radar(conn, slug)
    if cur is None:
        raise LookupError(f"no radar {slug!r}")
    sets, args = [], []
    for col, val in (("title", title), ("subtitle", subtitle), ("jira", jira)):
        if val is _KEEP:
            continue
        if col == "title" and not (val or "").strip():
            raise ValueError("a radar needs a title")
        sets.append(f"{col} = ?")
        args.append(val.strip() if isinstance(val, str) else val)
    if not sets:
        return cur
    sets.append("updated_at = ?")
    args.append(now_iso())
    conn.execute(f"UPDATE radar SET {', '.join(sets)} WHERE slug = ?", (*args, slug))
    conn.commit()
    return get_radar(conn, slug)


def delete_radar(conn, slug) -> dict:
    """Delete a radar and everything hanging off it, innermost first.

    SQLite does not enforce these foreign keys (no `PRAGMA foreign_keys` and no REFERENCES
    clauses), so the cascade is written out rather than assumed. Deleting the radar row
    alone would leave blips, moves and evidence that no query can ever reach again.
    """
    if get_radar(conn, slug) is None:
        raise LookupError(f"no radar {slug!r}")
    blips = [b["id"] for b in blips_of(conn, slug)]
    moves = [m["id"] for m in all_moves_of_radar(conn, slug)]
    if blips:
        marks = ", ".join("?" for _ in blips)
        conn.execute(
            f"DELETE FROM radar_blip_link WHERE blip IN ({marks}) OR related IN ({marks})",
            (*blips, *blips))
    ev = 0
    if moves:
        marks = ", ".join("?" for _ in moves)
        ev = conn.execute(
            f"DELETE FROM radar_evidence WHERE move IN ({marks})", tuple(moves)).rowcount
        conn.execute(f"DELETE FROM radar_move WHERE id IN ({marks})", tuple(moves))
    conn.execute("DELETE FROM radar_blip WHERE radar = ?", (slug,))
    conn.execute("DELETE FROM radar WHERE slug = ?", (slug,))
    conn.commit()
    return {"radar": slug, "blips": len(blips), "moves": len(moves), "evidence": max(ev, 0)}


# --- blips --------------------------------------------------------------------

def add_blip(conn, *, radar, num=None, name, quadrant, description=None, ref=None) -> dict:
    """Add a blip. `num=None` takes the next free number rather than making the
    caller read the radar first and race with anyone else adding one.

    `description` is what the thing IS, and it lives here rather than on a move for a
    reason: a definition is not a decision. On a move it would change every time the
    ring changed, and the same sentence would repeat in every row of the history.
    """
    if quadrant not in QUADRANTS:
        raise ValueError(f"unknown quadrant {quadrant!r} — one of {', '.join(QUADRANTS)}")
    if not (name or "").strip():
        raise ValueError("a blip needs a name")
    if get_radar(conn, radar) is None:
        raise LookupError(f"no radar {radar!r} — create it before adding blips to it")
    num = next_num(conn, radar) if num is None else int(num)
    _refuse_taken_num(conn, radar, num)
    bid = new_id("blip")
    conn.execute(
        "INSERT INTO radar_blip (id, radar, num, name, quadrant, created_at, description, ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (bid, radar, num, name.strip(), quadrant, now_iso(),
         (description or "").strip() or None, (ref or "").strip() or None))
    _touch_radar(conn, radar)
    conn.commit()
    return blip_by_id(conn, bid)


def blips_of(conn, radar) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_BLIP_SELECT} FROM radar_blip WHERE radar = ? ORDER BY num",
        (radar,)).fetchall()
    return [dict(zip(BLIP_COLS, r)) for r in rows]


def blip_by_num(conn, radar, num) -> dict | None:
    row = conn.execute(
        f"SELECT {_BLIP_SELECT} FROM radar_blip WHERE radar = ? AND num = ?",
        (radar, int(num))).fetchone()
    return dict(zip(BLIP_COLS, row)) if row else None


def blip_by_id(conn, blip_id) -> dict | None:
    row = conn.execute(
        f"SELECT {_BLIP_SELECT} FROM radar_blip WHERE id = ?", (blip_id,)).fetchone()
    return dict(zip(BLIP_COLS, row)) if row else None


def next_num(conn, radar) -> int:
    """The lowest free number on a radar, so a caller does not have to guess one.

    MAX+1 rather than COUNT+1: after a delete those differ, and COUNT+1 would collide
    with an existing blip.
    """
    row = conn.execute("SELECT MAX(num) FROM radar_blip WHERE radar = ?", (radar,)).fetchone()
    return int(row[0] or 0) + 1


def update_blip(conn, blip_id, *, name=_KEEP, quadrant=_KEEP, num=_KEEP,
                description=_KEEP, ref=_KEEP) -> dict:
    """Rename a blip, move it to another quadrant, renumber it, or correct what it is.

    None of these is history: a blip's name, quadrant, number, definition and link are
    LABELS, and correcting a label is not the same act as changing where it stands. Its
    ring is not settable here at all — that requires a move, which requires a reason.
    """
    cur = blip_by_id(conn, blip_id)
    if cur is None:
        raise LookupError(f"no blip {blip_id!r}")
    sets, args = [], []
    if name is not _KEEP:
        if not (name or "").strip():
            raise ValueError("a blip needs a name")
        sets.append("name = ?")
        args.append(name.strip())
    for col, val in (("description", description), ("ref", ref)):
        if val is _KEEP:
            continue
        sets.append(f"{col} = ?")
        # Empty string clears it, so a caller can remove a definition without needing
        # to know that null and "" are different here.
        args.append((val or "").strip() or None if isinstance(val, str) else val)
    if quadrant is not _KEEP:
        if quadrant not in QUADRANTS:
            raise ValueError(f"unknown quadrant {quadrant!r} — one of {', '.join(QUADRANTS)}")
        sets.append("quadrant = ?")
        args.append(quadrant)
    if num is not _KEEP and int(num) != cur["num"]:
        _refuse_taken_num(conn, cur["radar"], int(num))
        sets.append("num = ?")
        args.append(int(num))
    if not sets:
        return cur
    conn.execute(f"UPDATE radar_blip SET {', '.join(sets)} WHERE id = ?", (*args, blip_id))
    _touch_radar(conn, cur["radar"])
    conn.commit()
    return blip_by_id(conn, blip_id)


def _refuse_taken_num(conn, radar, num) -> None:
    """A readable refusal instead of the driver's IntegrityError.

    The unique index would stop the collision anyway, but "UNIQUE constraint failed:
    radar_blip.radar, radar_blip.num" does not tell a caller WHICH blip is in the way,
    and that is the only fact it needs to pick another number.
    """
    taken = blip_by_num(conn, radar, num)
    if taken is not None:
        raise ValueError(
            f"number {num} on radar {radar!r} is already blip {taken['name']!r} — "
            f"pick another, or call reindex_blips to close the gaps")


def set_related(conn, blip_id, related_ids) -> list[str]:
    """Replace a blip's stated relations. Returns the ids actually linked.

    Self-links are dropped rather than refused: "related to itself" is a slip, not a
    decision worth an error, and silently keeping it would put the blip in its own
    Related list.
    """
    if blip_by_id(conn, blip_id) is None:
        raise LookupError(f"no blip {blip_id!r}")
    wanted = [i for i in dict.fromkeys(related_ids or []) if i != blip_id]
    for i in wanted:
        if blip_by_id(conn, i) is None:
            raise LookupError(f"no blip {i!r} to relate to")
    # Only the rows this blip STATED are replaced. A relation stated from the other side
    # is that blip's row, and clearing this one's list must not silently delete it.
    conn.execute("DELETE FROM radar_blip_link WHERE blip = ?", (blip_id,))
    for i in wanted:
        conn.execute("INSERT INTO radar_blip_link (blip, related) VALUES (?, ?)",
                     (blip_id, i))
    row = blip_by_id(conn, blip_id)
    if row:
        _touch_radar(conn, row["radar"])
    conn.commit()
    return wanted


def related_of(conn, blip_ids) -> dict[str, list[str]]:
    """Related ids per blip, read in BOTH directions, in ONE query.

    Only ids: the related blips' RINGS are derived, and resolving them here would be a
    second derivation living outside `service`. The caller already holds the board.
    """
    ids = list(blip_ids)
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT blip, related FROM radar_blip_link "
        f"WHERE blip IN ({marks}) OR related IN ({marks})",
        (*ids, *ids)).fetchall()
    want = set(ids)
    out: dict[str, list[str]] = {}
    for a, b in rows:
        if a in want and b not in out.setdefault(a, []):
            out[a].append(b)
        if b in want and a not in out.setdefault(b, []):
            out[b].append(a)
    return out


def delete_blip(conn, blip_id) -> dict:
    """Delete a blip with its whole history. Cascade written out, same as delete_radar."""
    cur = blip_by_id(conn, blip_id)
    if cur is None:
        raise LookupError(f"no blip {blip_id!r}")
    # Both directions: a link stated FROM another blip would otherwise survive and point
    # at a row that no longer exists, and `related_of` reads both sides.
    conn.execute("DELETE FROM radar_blip_link WHERE blip = ? OR related = ?",
                 (blip_id, blip_id))
    moves = [m["id"] for m in moves_of(conn, blip_id)]
    ev = 0
    if moves:
        marks = ", ".join("?" for _ in moves)
        ev = conn.execute(
            f"DELETE FROM radar_evidence WHERE move IN ({marks})", tuple(moves)).rowcount
        conn.execute(f"DELETE FROM radar_move WHERE id IN ({marks})", tuple(moves))
    conn.execute("DELETE FROM radar_blip WHERE id = ?", (blip_id,))
    _touch_radar(conn, cur["radar"])
    conn.commit()
    return {"blip": blip_id, "num": cur["num"], "name": cur["name"],
            "moves": len(moves), "evidence": max(ev, 0)}


def reindex_blips(conn, radar, by="num") -> dict:
    """Renumber a radar's blips to 1..N with no gaps.

    Needed because deleting a blip leaves a hole, and the numbers are what a reader
    calls a blip by — "blip 5" on a radar whose numbers run 1,2,4,7 is a worse handle
    than it looks.

    Done in TWO passes through negative numbers. Renumbering in place one row at a
    time collides with the unique index the moment a blip takes a number another blip
    still holds (closing a gap always does this), and the collision is mid-statement
    so half the radar would already have moved. Negatives are free: no real blip can
    hold one, so the first pass can never conflict.

    `by="quadrant"` numbers the quadrants in drawing order instead, which makes the
    numbers scan in the same order as the circle rather than in insertion order.
    """
    blips = blips_of(conn, radar)
    if not blips:
        return {"radar": radar, "renumbered": 0, "changed": 0}
    if by == "quadrant":
        blips.sort(key=lambda b: (QUADRANTS.index(b["quadrant"])
                                  if b["quadrant"] in QUADRANTS else len(QUADRANTS),
                                  b["num"]))
    elif by != "num":
        raise ValueError(f"unknown ordering {by!r} — 'num' or 'quadrant'")
    plan = [(b["id"], b["num"], i + 1) for i, b in enumerate(blips)]
    changed = [(bid, old, new) for bid, old, new in plan if old != new]
    if not changed:
        return {"radar": radar, "renumbered": len(plan), "changed": 0}
    for bid, _old, new in changed:
        conn.execute("UPDATE radar_blip SET num = ? WHERE id = ?", (-new, bid))
    for bid, _old, new in changed:
        conn.execute("UPDATE radar_blip SET num = ? WHERE id = ?", (new, bid))
    _touch_radar(conn, radar)
    conn.commit()
    return {"radar": radar, "renumbered": len(plan), "changed": len(changed),
            "moved": [{"name": next(b["name"] for b in blips if b["id"] == bid),
                       "from": old, "to": new} for bid, old, new in changed]}


def _touch_radar(conn, slug) -> None:
    """Bump `updated_at` so the radar list orders by real activity.

    Every write path goes through this rather than each one remembering: the list is
    ordered by `updated_at DESC`, so a radar edited today sorting below one untouched
    for a month is a bug the reader sees before anyone else does.
    """
    conn.execute("UPDATE radar SET updated_at = ? WHERE slug = ?", (now_iso(), slug))


# --- moves and evidence -------------------------------------------------------

def add_move(conn, *, blip, ring, period, why, evidence=None, session_id=None,
             ts=None) -> dict:
    """Record a move. Refuses one that states no reason.

    `ring=None` means "entered the radar" — the first move, which has a reason but no
    position yet. Every other move must name a real ring.

    Evidence is OPTIONAL and recommended, not required. It used to be refused without,
    which had a cost the refusal hid: the cheapest way past a hard gate is a citation
    that exists to satisfy it, and a radar of decorative references is worse than one
    that admits which decisions are still only argued. The REASON stays mandatory,
    because that is the irreducible part — a position nobody explained is the thing this
    store exists to make unrepresentable. Evidence strengthens a reason; it cannot
    replace one, so it is prompted for at the surfaces (see `radar-blips`) instead of
    demanded here.
    """
    if not (why or "").strip():
        raise ValueError("a move needs a reason: `why` is required")
    if ring is not None and ring not in RINGS:
        raise ValueError(f"unknown ring {ring!r} — one of {', '.join(RINGS)}")
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
    row = blip_by_id(conn, blip)
    if row:
        _touch_radar(conn, row["radar"])
    conn.commit()
    return {"id": mid, "blip": blip, "ring": ring, "period": period, "why": why.strip()}


def moves_of(conn, blip) -> list[dict]:
    """Newest first — the order the panel reads them in.

    `id DESC` is a tiebreak, not a second sort key with meaning. Timestamps are
    microsecond-precise so a tie is now practically unreachable, but an *arbitrary*
    order and an *unstable* one are different failures: unstable means two reads of the
    same history can disagree, and the whole page exists to answer "did it move?".
    """
    rows = conn.execute(
        "SELECT id, ring, period, why, ts, session_id FROM radar_move "
        "WHERE blip = ? ORDER BY ts DESC, id DESC", (blip,)).fetchall()
    cols = ("id", "ring", "period", "why", "ts", "session_id")
    return [dict(zip(cols, r)) for r in rows]


def get_move(conn, move_id) -> dict | None:
    row = conn.execute(
        "SELECT id, blip, ring, period, why, ts, session_id FROM radar_move WHERE id = ?",
        (move_id,)).fetchone()
    cols = ("id", "blip", "ring", "period", "why", "ts", "session_id")
    return dict(zip(cols, row)) if row else None


def update_move(conn, move_id, *, ring=_KEEP, period=_KEEP, why=_KEEP) -> dict:
    """Correct a recorded move.

    This is the one write that edits history rather than appending to it, so it re-checks
    what `add_move` enforces rather than trusting that a row which was once valid still
    is: a move may never end up with a blank reason.

    It no longer re-checks evidence, because `add_move` no longer demands any. That check
    existed to close one specific door — promoting an evidence-free entry move to a real
    ring — and with evidence optional there is no door there to close.
    """
    cur = get_move(conn, move_id)
    if cur is None:
        raise LookupError(f"no move {move_id!r}")
    new_ring = cur["ring"] if ring is _KEEP else ring
    new_why = cur["why"] if why is _KEEP else why
    if not (new_why or "").strip():
        raise ValueError("a move needs a reason: `why` cannot be cleared")
    if new_ring is not None and new_ring not in RINGS:
        raise ValueError(f"unknown ring {new_ring!r} — one of {', '.join(RINGS)}")
    sets, args = ["why = ?"], [new_why.strip()]
    if ring is not _KEEP:
        sets.append("ring = ?")
        args.append(new_ring)
    if period is not _KEEP:
        if not (period or "").strip():
            raise ValueError("a move needs a period")
        sets.append("period = ?")
        args.append(period.strip())
    conn.execute(f"UPDATE radar_move SET {', '.join(sets)} WHERE id = ?", (*args, move_id))
    blip = blip_by_id(conn, cur["blip"])
    if blip:
        _touch_radar(conn, blip["radar"])
    conn.commit()
    return get_move(conn, move_id)


def delete_move(conn, move_id) -> dict:
    """Delete a move and its evidence.

    Refuses the blip's LAST move. A blip with no moves has no position and no reason to
    be on the radar, and the drawing has nowhere to put it — `ringBand` falls back to
    the Adopt band, so an unplaced blip would be drawn as if it were adopted. Deleting
    the blip is the honest way to say "this does not belong here".
    """
    cur = get_move(conn, move_id)
    if cur is None:
        raise LookupError(f"no move {move_id!r}")
    siblings = moves_of(conn, cur["blip"])
    if len(siblings) <= 1:
        blip = blip_by_id(conn, cur["blip"])
        raise ValueError(
            f"this is the only move on blip {blip['num'] if blip else '?'} "
            f"({blip['name'] if blip else cur['blip']}) — deleting it would leave a blip "
            "with a position nobody decided. Delete the blip instead.")
    ev = conn.execute("DELETE FROM radar_evidence WHERE move = ?", (move_id,)).rowcount
    conn.execute("DELETE FROM radar_move WHERE id = ?", (move_id,))
    blip = blip_by_id(conn, cur["blip"])
    if blip:
        _touch_radar(conn, blip["radar"])
    conn.commit()
    return {"move": move_id, "blip": cur["blip"], "ring": cur["ring"],
            "period": cur["period"], "evidence": max(ev, 0)}


def add_evidence(conn, move_id, evidence) -> list[dict]:
    """Cite more for a move that is already recorded."""
    if get_move(conn, move_id) is None:
        raise LookupError(f"no move {move_id!r}")
    rows = list(evidence or [])
    if not rows:
        raise ValueError("nothing to add")
    for e in rows:
        if not (e.get("title") or "").strip():
            raise ValueError("every piece of evidence needs a title")
    for e in rows:
        conn.execute(
            "INSERT INTO radar_evidence (id, move, kind, title, ref, dated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("ev"), move_id, e.get("kind") or "note", e["title"].strip(),
             e.get("ref"), e.get("dated")))
    conn.commit()
    return evidence_of(conn, [move_id]).get(move_id, [])


def delete_evidence(conn, evidence_id) -> dict:
    """Remove one citation.

    No longer refuses the last one. It used to, to keep a positioned move from ending up
    with nothing behind it — but with evidence optional that state is reachable from
    `add_move` anyway, so refusing it here would only have made removing a WRONG citation
    harder than never adding one. Reports what it removed so a caller can see the move is
    now uncited.
    """
    row = conn.execute(
        "SELECT id, move, kind, title FROM radar_evidence WHERE id = ?",
        (evidence_id,)).fetchone()
    if row is None:
        raise LookupError(f"no evidence {evidence_id!r}")
    eid, move_id, kind, title = row
    move = get_move(conn, move_id)
    conn.execute("DELETE FROM radar_evidence WHERE id = ?", (eid,))
    blip = blip_by_id(conn, move["blip"]) if move else None
    if blip:
        _touch_radar(conn, blip["radar"])
    conn.commit()
    # `remaining` rather than a bare acknowledgement: with no refusal left, the only way a
    # caller learns it just left a positioned move uncited is if the answer says so.
    return {"evidence": eid, "move": move_id, "kind": kind, "title": title,
            "remaining": len(evidence_of(conn, [move_id]).get(move_id, []))}


def evidence_of(conn, move_ids) -> dict[str, list[dict]]:
    """Evidence for many moves in ONE query, keyed by move id.

    Batched deliberately: a per-move query turns one blip into a dozen round trips,
    and the panel always wants the whole history at once.
    """
    ids = list(move_ids)
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    # `id` is selected because a citation you can delete has to be addressable. It was
    # absent while the only reader was the drawing, which needs no handle on a row.
    rows = conn.execute(
        f"SELECT move, id, kind, title, ref, dated FROM radar_evidence "
        f"WHERE move IN ({marks}) ORDER BY dated DESC", tuple(ids)).fetchall()
    out: dict[str, list[dict]] = {}
    for move, eid, kind, title, ref, dated in rows:
        out.setdefault(move, []).append(
            {"id": eid, "kind": kind, "title": title, "ref": ref, "dated": dated})
    return out


def all_moves_of_radar(conn, radar) -> list[dict]:
    """Every move on a radar, newest first, with its blip. One query for the whole
    board, so the ring of every blip can be derived without N+1."""
    rows = conn.execute(
        "SELECT m.id, m.blip, m.ring, m.period, m.why, m.ts "
        "FROM radar_move m JOIN radar_blip b ON b.id = m.blip "
        "WHERE b.radar = ? ORDER BY m.ts DESC, m.id DESC", (radar,)).fetchall()
    cols = ("id", "blip", "ring", "period", "why", "ts")
    return [dict(zip(cols, r)) for r in rows]
