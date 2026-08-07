#!/usr/bin/env python3
"""Radar MCP server — stdio, newline-delimited JSON-RPC.

The agent-facing surface of the tech radar. Everything the browser can do plus the
things it cannot: create a radar, add a blip, correct a record, renumber the board.

Two properties are worth knowing before calling anything.

**A position cannot exist without its reason.** There is no "set ring" tool, and there
is no ring column to set. A blip's ring is the ring of its newest move, derived on
read. So `radar_blip_add` takes a `why` and `radar_move` takes a `why` plus evidence,
and both refuse without them. This is not validation politeness — the state "positioned
for no stated reason" is unrepresentable, which is the whole point of the feature.

**Writes answer "what does the radar say now".** Every write tool returns the DERIVED
blip or board, not the row it wrote. After a move you get the new ring and the new
direction as the server computed them, so there is nothing to assume and no follow-up
read to make.

The destructive tools (`radar_delete`, `radar_blip_delete`, `radar_move_delete`) all
require `confirm=true` and all report what they cascaded. Two of them refuse outright
in the cases that would leave the board lying: a blip's last move cannot be deleted,
and the only evidence behind a positioned move cannot be removed.

It runs outside FlightDeck's process, so it resolves its own configuration the same way
the treasures server does: repo-root `.env`, then config.toml through flightdeck.config.
`handle()` is a pure function of a request dict, which is what the tests exercise.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

# Allow `python .../radar/mcp_server.py` from any cwd: backend/ is two levels up from
# this file, and that is what makes `flightdeck` importable.
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flightdeck import config, db                     # noqa: E402
from flightdeck.radar import service, store           # noqa: E402

_state = {"cfg": None, "conn": None, "used_at": 0.0}

# Same reclaim policy as the treasures server, and for the same measured reason: an MCP
# server lives as long as its Claude Code session, so a held write connection becomes
# one idle PostgreSQL connection per parked session, growing and never shrinking.
_IDLE_TTL = float(os.environ.get("RADAR_CONN_IDLE_TTL", "180"))
_REAP_EVERY = 20.0
_lock = threading.RLock()


def _load_dotenv() -> None:
    """systemd injects .env for the web app; an MCP server gets no such help."""
    env_path = BACKEND.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def configure(cfg: dict | None = None) -> None:
    """Wire the engine + schema once. Tests pass an explicit cfg."""
    if cfg is None:
        _load_dotenv()
        cfg = config.load(os.environ.get(
            "TOKEN_AUDIT_CONFIG", str(BACKEND / "config.toml")))
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    try:
        store.init(conn)
    finally:
        conn.close()
    with _lock:
        _state["cfg"] = cfg
        _state["conn"] = None
        _state["used_at"] = 0.0
    _start_reaper()


def _conn():
    with _lock:
        if _state["cfg"] is None:
            configure()
        if _state["conn"] is None:
            _state["conn"] = db.open_write(_state["cfg"]["db_path"])
        _state["used_at"] = time.monotonic()
        return _state["conn"]


def release_idle(ttl: float = None) -> bool:
    """Close the write connection if unused for `ttl` seconds. True when it closed."""
    limit = _IDLE_TTL if ttl is None else ttl
    with _lock:
        conn = _state["conn"]
        if conn is None or time.monotonic() - _state["used_at"] < limit:
            return False
        _state["conn"] = None
        try:
            conn.close()
        except Exception:
            pass
        return True


def _start_reaper() -> None:
    if _state.get("reaper"):
        return

    def loop():
        while True:
            time.sleep(_REAP_EVERY)
            try:
                release_idle()
            except Exception:
                pass

    thread = threading.Thread(target=loop, name="radar-conn-reaper", daemon=True)
    _state["reaper"] = thread
    thread.start()


# --- helpers ------------------------------------------------------------------

def _board(slug):
    board = service.radar_board(_conn(), slug)
    if board is None:
        raise LookupError(f"no radar {slug!r}")
    return board


def _need_confirm(what):
    return {"error": f"refused: pass confirm=true to permanently delete {what}",
            "confirmed": False}


def _kw(**pairs):
    """Forward only the fields the caller actually passed.

    The default is `store.KEEP`, not None, and that distinction is load-bearing. JSON
    null arrives as Python None and MEANS something on two of these fields: `ring: null`
    records an entry move, and `subtitle: null` clears a subtitle. A sentinel of None
    would swallow both — the ring could never be set back to unplaced and a subtitle
    could never be removed. A sentinel object cannot be produced by JSON at all, so
    "omitted" and "explicitly null" stay separable.
    """
    return {k: v for k, v in pairs.items() if v is not store.KEEP}


# --- reads --------------------------------------------------------------------

def radar_list():
    conn = _conn()
    return {"radars": [service.radar_board(conn, r["slug"])
                       for r in store.list_radars(conn)]}


def radar_get(slug):
    return _board(slug)


def radar_blip(slug, num):
    detail = service.blip_detail(_conn(), slug, int(num))
    if detail is None:
        raise LookupError(f"no blip {num} on radar {slug!r}")
    return detail


# --- radars -------------------------------------------------------------------

def radar_create(slug, title, subtitle=None, jira=None):
    conn = _conn()
    if store.get_radar(conn, slug) is not None:
        return {"error": f"radar {slug!r} already exists — use radar_update to change it"}
    store.upsert_radar(conn, slug=slug, title=title, subtitle=subtitle, jira=jira)
    return _board(slug)


def radar_update(slug, title=store.KEEP, subtitle=store.KEEP, jira=store.KEEP):
    store.update_radar(_conn(), slug, **_kw(title=title, subtitle=subtitle, jira=jira))
    return _board(slug)


def radar_delete(slug, confirm=False):
    if not confirm:
        board = _board(slug)
        return _need_confirm(
            f"radar {slug!r} with its {board['blipCount']} blips and "
            f"{board['moveCount']} moves")
    return store.delete_radar(_conn(), slug)


# --- blips --------------------------------------------------------------------

def radar_blip_add(slug, name, quadrant, why, period, num=None, ring=None,
                   evidence=None, session_id=None):
    return service.add_blip(_conn(), slug, name=name, quadrant=quadrant, why=why,
                            period=period, num=num, ring=ring, evidence=evidence,
                            session_id=session_id)


def radar_blip_update(slug, num, name=store.KEEP, quadrant=store.KEEP, new_num=store.KEEP):
    return service.update_blip(_conn(), slug, int(num),
                               **_kw(name=name, quadrant=quadrant, new_num=new_num))


def radar_blip_delete(slug, num, confirm=False):
    if not confirm:
        b = radar_blip(slug, int(num))
        return _need_confirm(
            f"blip {num} ({b['name']}) with its {b['moveCount']} moves and "
            f"{b['evidenceCount']} pieces of evidence")
    return service.delete_blip(_conn(), slug, int(num))


def radar_reindex(slug, by="num"):
    result = store.reindex_blips(_conn(), slug, by=by)
    return {**result, "board": _board(slug)}


# --- moves and evidence -------------------------------------------------------

def radar_move(slug, num, ring, period, why, evidence, session_id=None):
    return service.move_blip(_conn(), slug, int(num), ring=ring, period=period,
                             why=why, evidence=evidence, session_id=session_id)


def radar_move_update(slug, num, move_id, ring=store.KEEP, period=store.KEEP, why=store.KEEP):
    return service.update_move(_conn(), slug, int(num), move_id,
                               **_kw(ring=ring, period=period, why=why))


def radar_move_delete(slug, num, move_id, confirm=False):
    if not confirm:
        move = store.get_move(_conn(), move_id)
        if move is None:
            raise LookupError(f"no move {move_id!r}")
        return _need_confirm(
            f"the move to {move['ring'] or 'entered'} in {move['period']} "
            f"({move['why'][:60]}…) and its evidence")
    return service.delete_move(_conn(), slug, int(num), move_id)


def radar_evidence_add(slug, num, move_id, evidence):
    return service.add_evidence(_conn(), slug, int(num), move_id, evidence)


def radar_evidence_delete(slug, num, evidence_id):
    return service.delete_evidence(_conn(), slug, int(num), evidence_id)


_RING = {"type": ["string", "null"], "enum": [*store.RINGS, None],
         "description": "null records an ENTRY — on the radar, position not yet "
                        "decided. That is the only move allowed to cite no evidence."}
_QUADRANT = {"type": "string", "enum": list(store.QUADRANTS)}
_EVIDENCE = {
    "type": "array",
    "description": "what justifies the move. At least one is required for any move "
                   "that names a ring; the store refuses otherwise.",
    "items": {"type": "object",
              "properties": {"kind": {"type": "string",
                                      "enum": ["treasure", "trace", "jira", "note"]},
                             "title": {"type": "string"},
                             "ref": {"type": "string",
                                     "description": "path, URL or ticket key"},
                             "dated": {"type": "string",
                                       "description": "YYYY-MM-DD; drives staleness"}},
              "required": ["title"]},
}
_SESSION = {"type": "string",
            "description": "your Claude session id. Pass it and the move is traceable "
                           "back to the session that made it; omit it and the record "
                           "says a decision happened but not who made it."}

TOOLS = {
    "radar_list": (
        radar_list,
        "Every radar with its derived board — blip count, move count, ring "
        "distribution, stale count and history periods. The place to start.",
        {}, []),
    "radar_get": (
        radar_get,
        "One radar's whole board: every blip with its derived ring, movement "
        "direction, evidence age and newest reason, plus ring totals and the history "
        "periods. One call, three queries — do not loop radar_blip to build this.",
        {"slug": {"type": "string"}}, ["slug"]),
    "radar_blip": (
        radar_blip,
        "One blip in full: its derived position plus every move newest-first, each "
        "with its reason and evidence. Evidence rows carry `id`, which is what "
        "radar_evidence_delete takes.",
        {"slug": {"type": "string"}, "num": {"type": "integer"}}, ["slug", "num"]),
    "radar_create": (
        radar_create,
        "Create a radar. The slug is its identity and is not editable afterwards — it "
        "is in every URL and on every blip row — so choose it deliberately. Refuses "
        "an existing slug rather than silently overwriting it.",
        {"slug": {"type": "string",
                  "description": "kebab-case, e.g. 'subscription-migration'"},
         "title": {"type": "string"},
         "subtitle": {"type": "string"},
         "jira": {"type": "string", "description": "e.g. CRM-11197"}},
        ["slug", "title"]),
    "radar_update": (
        radar_update,
        "Change a radar's title, subtitle or Jira key. Omitted fields are left alone.",
        {"slug": {"type": "string"}, "title": {"type": "string"},
         "subtitle": {"type": "string"}, "jira": {"type": "string"}},
        ["slug"]),
    "radar_delete": (
        radar_delete,
        "Delete a radar with every blip, move and piece of evidence on it. Needs "
        "confirm=true; called without it, reports exactly how much would go.",
        {"slug": {"type": "string"},
         "confirm": {"type": "boolean", "description": "must be true"}},
        ["slug"]),
    "radar_blip_add": (
        radar_blip_add,
        "Put something on the radar, WITH the reason it is there. The blip and its "
        "first move are one act: a blip with no move has no position and no reason to "
        "exist, so `why` is required and this tool refuses without it.\n"
        "Omit num to take the next free number. Pass ring=null for an entry (position "
        "not yet decided, no evidence needed); name a ring and evidence becomes "
        "required.",
        {"slug": {"type": "string"}, "name": {"type": "string"},
         "quadrant": _QUADRANT,
         "why": {"type": "string",
                 "description": "why this is on the radar. This sentence is what the "
                                "radar shows a year from now, not the ring."},
         "period": {"type": "string", "description": "e.g. 'Q3 2026'"},
         "num": {"type": "integer", "description": "omit for the next free number"},
         "ring": _RING, "evidence": _EVIDENCE, "session_id": _SESSION},
        ["slug", "name", "quadrant", "why", "period"]),
    "radar_blip_update": (
        radar_blip_update,
        "Correct a blip's LABELS — its name, its quadrant, or its number. None of "
        "these is history. Its ring is deliberately not here: changing where something "
        "stands requires radar_move, which requires a reason.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "name": {"type": "string"}, "quadrant": _QUADRANT,
         "new_num": {"type": "integer",
                     "description": "renumber it; refused if another blip holds that "
                                    "number, naming which one"}},
        ["slug", "num"]),
    "radar_blip_delete": (
        radar_blip_delete,
        "Remove a blip and its whole history. Needs confirm=true; called without it, "
        "reports how many moves and citations would go with it.\n"
        "This — not radar_move_delete — is how you say 'this does not belong on the "
        "radar'.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "confirm": {"type": "boolean", "description": "must be true"}},
        ["slug", "num"]),
    "radar_reindex": (
        radar_reindex,
        "Renumber a radar's blips to 1..N with no gaps, and return the new board. "
        "Deleting a blip leaves a hole, and the numbers are what a reader calls a blip "
        "by. by='quadrant' numbers them in drawing order instead of insertion order, "
        "so the numbers scan the same way the circle does. Reports every blip that "
        "moved, from and to.",
        {"slug": {"type": "string"},
         "by": {"type": "string", "enum": ["num", "quadrant"]}},
        ["slug"]),
    "radar_move": (
        radar_move,
        "Move a blip to a ring, with the reason and the evidence. THE write verb: "
        "position is the newest move, so this is the only way a blip's ring changes.\n"
        "Re-selecting the ring it already holds is legitimate and meaningful — it "
        "records the position being HELD with fresh evidence, which is how a stale blip "
        "is refreshed without a demotion that never happened.",
        {"slug": {"type": "string"}, "num": {"type": "integer"}, "ring": _RING,
         "period": {"type": "string", "description": "e.g. 'Q3 2026'"},
         "why": {"type": "string",
                 "description": "required. What changed, and what it means for this "
                                "choice."},
         "evidence": _EVIDENCE, "session_id": _SESSION},
        ["slug", "num", "ring", "period", "why", "evidence"]),
    "radar_move_update": (
        radar_move_update,
        "Correct a move already on record — its reason, its ring, or its period. This "
        "is the one tool that edits history rather than appending to it, so prefer "
        "recording a NEW move when the world changed, and use this only when the "
        "record itself was wrong.\n"
        "Re-checks the same invariants on the result: the reason cannot be cleared, "
        "and a move that ends up naming a ring must still have evidence behind it.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "move_id": {"type": "string", "description": "from radar_blip"},
         "ring": _RING, "period": {"type": "string"}, "why": {"type": "string"}},
        ["slug", "num", "move_id"]),
    "radar_move_delete": (
        radar_move_delete,
        "Delete a move and its evidence. Needs confirm=true.\n"
        "Refuses a blip's LAST move: that would leave a blip whose position nobody "
        "decided, and the drawing has nowhere to put it. Delete the blip instead.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "move_id": {"type": "string"},
         "confirm": {"type": "boolean", "description": "must be true"}},
        ["slug", "num", "move_id"]),
    "radar_evidence_add": (
        radar_evidence_add,
        "Cite more for a move already on record. The way to refresh a blip flagged "
        "stale without touching its position — staleness is a property of the "
        "evidence, not of the blip.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "move_id": {"type": "string"}, "evidence": _EVIDENCE},
        ["slug", "num", "move_id", "evidence"]),
    "radar_evidence_delete": (
        radar_evidence_delete,
        "Remove one citation by its id (from radar_blip). Refuses to leave a "
        "POSITIONED move with none — add replacement evidence first, or delete the "
        "move.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "evidence_id": {"type": "string"}},
        ["slug", "num", "evidence_id"]),
}


def handle(req: dict):
    """Map one JSON-RPC request to a response dict (None for notifications)."""
    mid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "radar", "version": "0.1"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r}}
            for n, (_f, d, p, r) in TOOLS.items()]}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        entry = TOOLS.get(name)
        try:
            out = entry[0](**args) if entry else {"error": f"unknown tool {name}"}
        except Exception as e:                      # surface as data, never crash
            out = {"error": f"{type(e).__name__}: {e}"}
        finally:
            # Commit after EVERY call, read or write. The write connection is not
            # autocommit, so a read-only call would otherwise leave the session "idle
            # in transaction" holding a lock — the treasures server learned this by
            # blocking an unrelated migration for 14 hours.
            if _state["conn"] is not None:
                try:
                    _state["conn"].commit()
                except Exception:
                    pass
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
