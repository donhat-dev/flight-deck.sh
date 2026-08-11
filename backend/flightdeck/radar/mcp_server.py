#!/usr/bin/env python3
"""Radar MCP server — stdio, newline-delimited JSON-RPC.

The agent-facing surface of the tech radar. Everything the browser can do plus the
things it cannot: create a radar, add a blip, correct a record, renumber the board.

Two properties are worth knowing before calling anything.

**A position cannot exist without its reason.** There is no "set ring" tool, and there
is no ring column to set. A blip's ring is the ring of its newest move, derived on
read. So `radar_blip_add` and `radar_move` both take a `why`, and both refuse without one.
This is not validation politeness — the state "positioned for no stated reason" is
unrepresentable, which is the whole point of the feature.

Evidence is the part that is NOT enforced. It is optional, and worth OFFERING when a blip
changes ring or lands somewhere consequential (Adopt, Caution). A citation supplied to get
past a gate says nothing, and an honest gap is easier to fix later than a decorative
reference nobody ever checks.

**Writes answer "what does the radar say now".** Every write tool returns the DERIVED
blip or board, not the row it wrote. After a move you get the new ring and the new
direction as the server computed them, so there is nothing to assume and no follow-up
read to make.

The destructive tools (`radar_delete`, `radar_blip_delete`, `radar_move_delete`) all
require `confirm=true` and all report what they cascaded. One of them refuses outright in
the case that would leave the board lying: a blip's last move cannot be deleted, because a
blip with no move has a position nobody decided.

The runtime — config resolution, connection lifecycle, the idle reaper, the
commit-after-every-call rule — lives in `flightdeck.agentsurface.runtime`, shared with
every other domain. This module holds only what is radar-specific: the tool functions
and the TOOLS table the registry collects. `handle()` and `main()` remain as a scoped
compatibility server, because a Claude session that spawned the old `radar` .mcp.json
entry keeps running this file until that session ends.
"""
import sys
from pathlib import Path

# Allow `python .../radar/mcp_server.py` from any cwd: backend/ is two levels up from
# this file, and that is what makes `flightdeck` importable.
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flightdeck.agentsurface import registry, runtime  # noqa: E402
from flightdeck.radar import service, store            # noqa: E402

# The shared runtime, under the names this module always exported. Tests reach through
# these (`_state["conn"] = Spy()`, `release_idle(ttl=0)`), and keeping them is what let
# the consolidation land without rewriting two test suites.
configure = runtime.configure
release_idle = runtime.release_idle
_state = runtime._state
_conn = runtime.conn


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

def radar_create(slug, title, subtitle=None, jira=None, quadrants=None):
    conn = _conn()
    if store.get_radar(conn, slug) is not None:
        return {"error": f"radar {slug!r} already exists — use radar_update to change it"}
    store.upsert_radar(conn, slug=slug, title=title, subtitle=subtitle, jira=jira,
                       quadrant_labels=quadrants)
    return _board(slug)


def radar_update(slug, title=store.KEEP, subtitle=store.KEEP, jira=store.KEEP,
                 quadrants=store.KEEP):
    store.update_radar(_conn(), slug, **_kw(title=title, subtitle=subtitle, jira=jira,
                                            quadrant_labels=quadrants))
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
                   evidence=None, session_id=None, description=None, ref=None,
                   related=None):
    return service.add_blip(_conn(), slug, name=name, quadrant=quadrant, why=why,
                            period=period, num=num, ring=ring, evidence=evidence,
                            session_id=session_id, description=description, ref=ref,
                            related=related)


def radar_blip_update(slug, num, name=store.KEEP, quadrant=store.KEEP, new_num=store.KEEP,
                      description=store.KEEP, ref=store.KEEP, related=store.KEEP):
    return service.update_blip(_conn(), slug, int(num),
                               **_kw(name=name, quadrant=quadrant, new_num=new_num,
                                     description=description, ref=ref, related=related))


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

def radar_move(slug, num, ring, period, why, evidence=None, session_id=None):
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
_QUADRANT = {"type": "string", "enum": list(store.QUADRANTS),
             "description": "quadrant KEY. Keys are permanent addresses; what each one "
                            "MEANS on this radar is its per-radar label — read "
                            "board.quadrants. The migration genre maps lang → Convention."}
_QUADRANT_LABELS = {
    "type": "object",
    "description": "per-radar quadrant labels, {key: label} with keys from "
                   f"{'/'.join(store.QUADRANTS)}. Omitted keys keep their classic label; "
                   "null clears every override. The migration genre (see the radar-blips "
                   "skill) uses {platforms: Systems, techniques: Techniques, tools: Tools, "
                   "lang: Convention}.",
    "additionalProperties": {"type": "string"},
}
_EVIDENCE = {
    "type": "array",
    "description": "what justifies the move. OPTIONAL — the store accepts a move with "
                   "none, because a citation added to satisfy a check is worth less than "
                   "an honest gap. Offer to fill it in two cases: the blip actually "
                   "CHANGES ring, or the choice is consequential (a move into Adopt or "
                   "Caution, which is what other work will be built on or steered away "
                   "from). For a hold, or an entry with nothing decided yet, do not ask.",
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
_DESCRIPTION = {
    "type": "string",
    "description": "What the thing is and what it does in our system, in plain words, "
                   "independent of any ring. This is a property of the blip and not of "
                   "a move: a definition is not a decision, so it must not change when "
                   "the position changes. Keep the argument for the ring in the move's "
                   "`why` instead. MARKDOWN SUBSET, never HTML: **bold**, *italic*, "
                   "`code`, [links](https://…), `- ` lists; wrap XML/HTML examples in "
                   "backticks — raw tags are refused on write.",
}
_REF = {"type": "string",
        "description": "one external link — repo, docs, or spec. Evidence goes on the "
                       "move that cited it, not here."}
_RELATED = {
    "type": "array",
    "items": {"type": "integer"},
    "description": "blip NUMBERS on this radar to show as related. REPLACES the whole "
                   "set, so pass the full list. Relations are read in both directions: "
                   "stating 5 relates to 20 also shows 5 on blip 20.",
}

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
         "jira": {"type": "string", "description": "e.g. CRM-11197"},
         "quadrants": _QUADRANT_LABELS},
        ["slug", "title"]),
    "radar_update": (
        radar_update,
        "Change a radar's title, subtitle, Jira key or quadrant labels. Omitted "
        "fields are left alone.",
        {"slug": {"type": "string"}, "title": {"type": "string"},
         "subtitle": {"type": "string"}, "jira": {"type": "string"},
         "quadrants": _QUADRANT_LABELS},
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
                 "description": "why this is on the radar: the reason for the position, "
                                "the main evidence, and any real trade-off. Markdown "
                                "subset, never HTML."},
         "period": {"type": "string", "description": "e.g. 'Q3 2026'"},
         "num": {"type": "integer", "description": "omit for the next free number"},
         "ring": _RING, "evidence": _EVIDENCE, "session_id": _SESSION,
         "description": _DESCRIPTION, "ref": _REF, "related": _RELATED},
        ["slug", "name", "quadrant", "why", "period"]),
    "radar_blip_update": (
        radar_blip_update,
        "Correct a blip's LABELS — its name, quadrant, number, definition, link, or "
        "related list. None of these is history. Its ring is deliberately not here: "
        "changing where something stands requires radar_move, which requires a reason.\n"
        "Omitted fields are left alone; an empty string clears `description` or `ref`.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "name": {"type": "string"}, "quadrant": _QUADRANT,
         "new_num": {"type": "integer",
                     "description": "renumber it; refused if another blip holds that "
                                    "number, naming which one"},
         "description": _DESCRIPTION, "ref": _REF, "related": _RELATED},
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
        "records the position being HELD, which is how a stale blip is refreshed without "
        "a demotion that never happened.\n"
        "Evidence is OPTIONAL. Offer to fill it when the ring actually changes, or when "
        "the landing is consequential (Adopt, Caution). Never block a move on it.",
        {"slug": {"type": "string"}, "num": {"type": "integer"}, "ring": _RING,
         "period": {"type": "string", "description": "e.g. 'Q3 2026'"},
         "why": {"type": "string",
                 "description": "required. What changed, and what it means for this "
                                "choice. Markdown subset, never HTML."},
         "evidence": _EVIDENCE, "session_id": _SESSION},
        ["slug", "num", "ring", "period", "why"]),
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
        "Remove one citation by its id (from radar_blip). Accepts leaving the move with "
        "none, and reports `remaining` so a caller can see it just left a positioned move "
        "uncited.",
        {"slug": {"type": "string"}, "num": {"type": "integer"},
         "evidence_id": {"type": "string"}},
        ["slug", "num", "evidence_id"]),
}


def handle(req: dict):
    """The scoped compatibility server: radar tools only, under the old server name."""
    return registry.handle(req, TOOLS, server="radar")


def main() -> None:
    registry.serve_stdio(TOOLS, server="radar")


if __name__ == "__main__":
    main()
