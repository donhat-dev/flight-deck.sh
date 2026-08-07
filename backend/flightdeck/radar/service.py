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
        # What it IS and where to read more, both independent of any move. `.get` rather
        # than `[...]`: a caller can hand in a hand-built blip dict, and these two are
        # the only fields the store added after the first readers existed.
        "description": blip.get("description"),
        "ref": blip.get("ref"),
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
    views, moves = _views_of(conn, slug, today)
    _attach_related(conn, views)
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


def _views_of(conn, slug, today=None):
    """Every blip on a radar as a resolved view, in THREE queries. Returns (views, moves).

    Extracted because `blip_detail` needs peer views too — its related blips are other
    blips, and each one's ring comes from its own newest move. Building them one at a
    time was two queries per blip, which is the N+1 this module's docstring exists to
    forbid; the count is now the same whether the radar holds three blips or thirty-four.
    """
    blips = store.blips_of(conn, slug)
    moves = store.all_moves_of_radar(conn, slug)
    ev = store.evidence_of(conn, [m["id"] for m in moves])
    by_blip: dict[str, list[dict]] = {}
    for m in moves:
        by_blip.setdefault(m["blip"], []).append(m)
    views = [blip_view(conn, b, moves=by_blip.get(b["id"], []), evidence=ev, today=today)
             for b in blips]
    return views, moves


def _attach_related(conn, views, extra=None) -> None:
    """Resolve each view's `related` to name + quadrant + DERIVED ring, in place.

    Here rather than in the store because a related blip's ring is the ring of its
    newest move — the same derivation the rest of this module owns. Resolving it in SQL
    would be a second implementation, and the two would disagree the first time one was
    corrected.

    `extra` supplies views for blips outside this set, which `blip_detail` needs: its
    related blips are usually not the one blip it fetched.
    """
    by_id = {v["id"]: v for v in [*views, *(extra or [])]}
    links = store.related_of(conn, [v["id"] for v in views])
    for v in views:
        v["related"] = [
            {"num": r["num"], "name": r["name"], "quadrant": r["quadrant"], "ring": r["ring"]}
            for r in (by_id.get(i) for i in links.get(v["id"], [])) if r
        ]


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
    # Peer views for the related list, batched. A related blip's ring is the ring of its
    # own newest move, so the peers have to be resolved the same way as the board does.
    peers, _ = _views_of(conn, slug, today)
    _attach_related(conn, [view], extra=[p for p in peers if p["id"] != view["id"]])
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
    return blip_detail(conn, slug, num)


# --- the write verbs an agent needs -------------------------------------------
#
# Each returns the DERIVED view rather than the row it wrote. The question after any
# write here is "what does the radar say now", and a caller handed back its own input
# has to make a second call to find out — or worse, assume.


def add_blip(conn, slug, *, name, quadrant, why, period, num=None, ring=None,
             evidence=None, session_id=None, description=None, ref=None,
             related=None) -> dict:
    """Put something on the radar, with the reason it is there.

    A blip and its first move are ONE act. A blip with no moves has no position and no
    reason to exist, so allowing the two to be separate calls would make that state
    reachable — and it is exactly the state `add_move` refuses to create for every
    later move.

    `ring=None` records an entry: on the radar, position not yet decided. That is the
    one move allowed to cite nothing, because nothing has been decided to cite for.
    """
    if store.get_radar(conn, slug) is None:
        raise LookupError(f"no radar {slug!r}")
    blip = store.add_blip(conn, radar=slug, num=num, name=name, quadrant=quadrant,
                          description=description, ref=ref)
    try:
        store.add_move(conn, blip=blip["id"], ring=ring, period=period, why=why,
                       evidence=evidence or [], session_id=session_id)
        if related:
            store.set_related(conn, blip["id"], _ids_for_nums(conn, slug, related))
    except Exception:
        # The move's rules live in add_move and are not duplicated here, so the only
        # way to honour them is to let it refuse and then undo the blip. Leaving the
        # blip behind would create the move-less blip this function exists to prevent.
        store.delete_blip(conn, blip["id"])
        raise
    return blip_detail(conn, slug, blip["num"])


def _ids_for_nums(conn, slug, nums) -> list[str]:
    """Blip NUMBERS in, ids out.

    Callers address blips by the number on the drawing, which is what a reader can see;
    ids are internal. A number with no blip is an error rather than a silent drop —
    "related to blip 12" quietly meaning nothing is worse than a refusal.
    """
    out = []
    for n in nums or []:
        b = store.blip_by_num(conn, slug, int(n))
        if b is None:
            raise LookupError(f"no blip {n} on radar {slug!r} to relate to")
        out.append(b["id"])
    return out


def update_blip(conn, slug, num, *, name=store.KEEP, quadrant=store.KEEP,
                new_num=store.KEEP, description=store.KEEP, ref=store.KEEP,
                related=store.KEEP) -> dict:
    """Correct a blip's labels. Its ring is not here — that needs a move."""
    blip = _blip(conn, slug, num)
    store.update_blip(conn, blip["id"], name=name, quadrant=quadrant, num=new_num,
                      description=description, ref=ref)
    # `related` REPLACES the set rather than adding to it, which is why it is not folded
    # in with the fields above: those are values, this is a whole list.
    if related is not store.KEEP:
        store.set_related(conn, blip["id"], _ids_for_nums(conn, slug, related or []))
    return blip_detail(conn, slug, store.blip_by_id(conn, blip["id"])["num"])


def delete_blip(conn, slug, num) -> dict:
    return store.delete_blip(conn, _blip(conn, slug, num)["id"])


def update_move(conn, slug, num, move_id, *, ring=store.KEEP, period=store.KEEP,
                why=store.KEEP) -> dict:
    _own_move(conn, slug, num, move_id)
    store.update_move(conn, move_id, ring=ring, period=period, why=why)
    return blip_detail(conn, slug, num)


def delete_move(conn, slug, num, move_id) -> dict:
    _own_move(conn, slug, num, move_id)
    removed = store.delete_move(conn, move_id)
    return {**removed, "blip": blip_detail(conn, slug, num)}


def add_evidence(conn, slug, num, move_id, evidence) -> dict:
    _own_move(conn, slug, num, move_id)
    store.add_evidence(conn, move_id, evidence)
    return blip_detail(conn, slug, num)


def delete_evidence(conn, slug, num, evidence_id) -> dict:
    removed = store.delete_evidence(conn, evidence_id)
    _own_move(conn, slug, num, removed["move"])
    return {**removed, "blip": blip_detail(conn, slug, num)}


def _blip(conn, slug, num) -> dict:
    blip = store.blip_by_num(conn, slug, num)
    if blip is None:
        raise LookupError(f"no blip {num} on radar {slug!r}")
    return blip


def _own_move(conn, slug, num, move_id) -> dict:
    """Check the move really belongs to that blip on that radar.

    Move ids are opaque and global, so without this a caller holding an id from one
    radar could edit it while addressing another — and the response, built from
    `slug`/`num`, would show the untouched blip and look like the edit did nothing.
    """
    blip = _blip(conn, slug, num)
    move = store.get_move(conn, move_id)
    if move is None:
        raise LookupError(f"no move {move_id!r}")
    if move["blip"] != blip["id"]:
        raise ValueError(
            f"move {move_id!r} does not belong to blip {num} on radar {slug!r}")
    return move
