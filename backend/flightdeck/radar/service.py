"""Radar derivations — everything the drawing needs that is not stored.

Three things are derived rather than kept, and each for the same reason: a stored
copy would be a second truth that drifts.

  ring      the ring of the newest move
  state     whether that move went inward, outward, entered, or held
  freshness how old the newest piece of evidence is

`state` is the one that has to be computed rather than recorded. "Moved inward" is
not a property of a move, it is the RELATION between two moves, and a move that
recorded its own direction would be wrong the moment an earlier move was corrected.
"""
from datetime import datetime, timezone

from flightdeck.radar import store

RING_ORDER = {"caution": 0, "assess": 1, "trial": 2, "adopt": 3}


def _direction(newer_ring, older_ring):
    """`in` toward Adopt, `out` toward Caution, `new` on entry, `held` otherwise."""
    if newer_ring is None:
        return "new"
    if older_ring is None:
        # First positioned move after entering: it arrived, it did not travel.
        return "new"
    a, b = RING_ORDER.get(newer_ring, -1), RING_ORDER.get(older_ring, -1)
    if a > b:
        return "in"
    if a < b:
        return "out"
    return "held"


def _age_days(dated, today=None):
    if not dated:
        return None
    try:
        d = datetime.fromisoformat(dated.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    now = today or datetime.now(timezone.utc)
    return max(0, (now - d).days)


def blip_view(conn, blip, moves=None, evidence=None, today=None) -> dict:
    """One blip, with its ring, state, and evidence age resolved."""
    ms = moves if moves is not None else store.moves_of(conn, blip["id"])
    ev = evidence if evidence is not None else store.evidence_of(conn, [m["id"] for m in ms])
    newest = ms[0] if ms else None
    # The previous POSITIONED move, not simply the previous move: an entry row has no
    # ring, and comparing against it would report every first placement as a move.
    prev = next((m for m in ms[1:] if m["ring"] is not None), None)
    flat = [e for m in ms for e in ev.get(m["id"], [])]
    ages = [a for a in (_age_days(e.get("dated"), today) for e in flat) if a is not None]
    return {
        "id": blip["id"],
        "num": blip["num"],
        "name": blip["name"],
        "quadrant": blip["quadrant"],
        "ring": newest["ring"] if newest else None,
        "state": _direction(newest["ring"] if newest else None,
                            prev["ring"] if prev else None),
        "period": newest["period"] if newest else None,
        "lastMove": (f"{newest['period']} → {newest['ring'].capitalize()}"
                     if newest and newest["ring"] else
                     (f"{newest['period']} → Entered" if newest else None)),
        "why": newest["why"] if newest else None,
        "moveCount": len(ms),
        "evidenceCount": len(flat),
        "evidenceAgeDays": min(ages) if ages else None,
    }


def radar_board(conn, slug, today=None) -> dict | None:
    """The whole radar in ONE pass.

    Three queries total — blips, moves, evidence — rather than three per blip. A
    34-blip radar would otherwise be a hundred round trips to draw one circle.
    """
    radar = store.get_radar(conn, slug)
    if radar is None:
        return None
    blips = store.blips_of(conn, slug)
    moves = store.all_moves_of_radar(conn, slug)
    ev = store.evidence_of(conn, [m["id"] for m in moves])
    by_blip: dict[str, list[dict]] = {}
    for m in moves:
        by_blip.setdefault(m["blip"], []).append(m)
    views = [blip_view(conn, b, moves=by_blip.get(b["id"], []), evidence=ev, today=today)
             for b in blips]
    rings = {r: sum(1 for v in views if v["ring"] == r) for r in store.RINGS}
    return {
        **radar,
        "blips": views,
        "rings": rings,
        "blipCount": len(views),
        "moveCount": len(moves),
        "stale": sum(1 for v in views if (v["evidenceAgeDays"] or 0) > 60),
        "periods": _periods(moves),
    }


def _periods(moves) -> list[dict]:
    """Move counts per period, oldest first — the history scrubber's stops.

    Derived from the moves themselves, so a period with no moves simply is not a
    stop. A hand-kept list would show quarters that never happened.
    """
    counts: dict[str, int] = {}
    for m in moves:
        counts[m["period"]] = counts.get(m["period"], 0) + 1
    keys = sorted(counts)
    return [{"key": k, "moves": counts[k], "current": k == keys[-1] if keys else False}
            for k in keys]


def blip_detail(conn, slug, num, today=None) -> dict | None:
    blip = store.blip_by_num(conn, slug, num)
    if blip is None:
        return None
    ms = store.moves_of(conn, blip["id"])
    ev = store.evidence_of(conn, [m["id"] for m in ms])
    view = blip_view(conn, blip, moves=ms, evidence=ev, today=today)
    return {
        **view,
        "moves": [{**m, "evidence": ev.get(m["id"], [])} for m in ms],
        "evidence": [e for m in ms for e in ev.get(m["id"], [])],
    }


def move_blip(conn, slug, num, *, ring, period, why, evidence, session_id=None) -> dict:
    """Move a blip, or raise with a reason a form can show."""
    blip = store.blip_by_num(conn, slug, num)
    if blip is None:
        raise LookupError(f"no blip {num} on radar {slug!r}")
    store.add_move(conn, blip=blip["id"], ring=ring, period=period, why=why,
                   evidence=evidence, session_id=session_id)
    store.upsert_radar(conn, slug=slug, title=store.get_radar(conn, slug)["title"],
                       subtitle=store.get_radar(conn, slug)["subtitle"],
                       jira=store.get_radar(conn, slug)["jira"])
    return blip_detail(conn, slug, num)
