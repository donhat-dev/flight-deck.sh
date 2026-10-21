"""One-off repair of a ledger written before the response key existed.

    cd python && ../.venv/bin/python -m flightdeck.backfill            # dry run
    cd python && ../.venv/bin/python -m flightdeck.backfill --apply

Three passes, in order:

1. **Re-scan every transcript still on disk** with the current ingest. That fills
   `message_id` / `request_id` on stored rows, marks further lines of the same
   response `dup_of` the first (exact), and builds turns and busy time.
2. **Collapse rows whose transcript is gone** (Claude Code deletes transcripts
   after about 30 days, so the ledger is the only copy of that history). Without
   a message id they are matched on their input-side token tuple: first within a
   session (the per-block repeats of one response, which differ in timestamp),
   then across sessions at the same timestamp (a resume/fork copy). Measured on the
   on-disk corpus against the exact key: 0.3-1.4% high per token class.
3. **Estimate busy time for sessions whose transcript is gone**, from billed rows
   and stored prompt text, stored with method 'ledger'.

Nothing is deleted: a repeated row gets `dup_of`, so the repair can be undone with
`UPDATE messages SET dup_of = NULL WHERE dedupe_method = 'heuristic'`.
"""
import argparse
import os
from datetime import datetime, timedelta

from flightdeck import activity, config, db, ingest, pricing

# Below this much cached context a token tuple is too common to match across
# sessions (short probe calls repeat exactly).
_CROSS_SESSION_MIN_CACHED = 10_000


def _totals(conn) -> dict:
    rows = conn.execute(
        "SELECT model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h, "
        "output_tokens, speed, inference_geo FROM messages WHERE dup_of IS NULL").fetchall()
    cost = raw = 0.0
    n = 0
    for r in rows:
        args = (r["model"], int(r["input_tokens"] or 0), int(r["cache_read"] or 0),
                int(r["cache_create_5m"] or 0), int(r["cache_create_1h"] or 0),
                int(r["output_tokens"] or 0), r["ts"], r["speed"], r["inference_geo"])
        c = pricing.message_cost(*args)
        if c is None:
            continue
        n += 1
        cost += c
        raw += pricing.uncached_cost(*args)
    return {"billed_rows": n, "cost": round(cost, 2), "uncached": round(raw, 2),
            "net_cache_savings": round(raw - cost, 2)}


def rescan(conn, projects_dir: str) -> int:
    conn.execute("DELETE FROM files")
    conn.commit()
    ingest._stat_cache.clear()
    return ingest.ingest_all(conn, projects_dir)


def collapse_heuristic(conn, apply: bool) -> dict:
    rows = conn.execute(
        "SELECT uuid, session_id, model, ts, input_tokens, cache_read, cache_create_5m, "
        "cache_create_1h, output_tokens, message_id FROM messages "
        "WHERE dup_of IS NULL AND model LIKE ?", ("claude-%",)).fetchall()
    rows = [dict(r) for r in rows]

    def tokens(r):
        return (int(r["input_tokens"] or 0), int(r["cache_read"] or 0),
                int(r["cache_create_5m"] or 0), int(r["cache_create_1h"] or 0))

    marks: dict[str, str] = {}      # uuid -> canonical uuid
    out_max: dict[str, int] = {}    # canonical uuid -> largest output in its group

    def merge(group):
        # Only rows without a message id are in doubt; two different message
        # ids are two responses and never merge.
        if not any(r["message_id"] is None for r in group):
            return
        group.sort(key=lambda r: (r["message_id"] is None, r["ts"] or "", r["uuid"]))
        keep = group[0]
        for r in group[1:]:
            if r["message_id"] is not None and r["message_id"] != keep["message_id"]:
                continue
            marks[r["uuid"]] = keep["uuid"]
        out_max[keep["uuid"]] = max(int(r["output_tokens"] or 0) for r in group
                                    if r["uuid"] == keep["uuid"] or marks.get(r["uuid"]) == keep["uuid"])

    # Stage A: repeats of one response inside a session.
    by_session: dict = {}
    for r in rows:
        by_session.setdefault((r["session_id"], r["model"]) + tokens(r), []).append(r)
    for group in by_session.values():
        if len(group) > 1:
            merge(group)
    survivors = [r for r in rows if r["uuid"] not in marks]
    for r in survivors:
        if r["uuid"] in out_max:
            r["output_tokens"] = out_max[r["uuid"]]
    # Stage B: resume/fork copies carry the original timestamp into a new session.
    by_copy: dict = {}
    for r in survivors:
        t = tokens(r)
        if t[1] + t[2] + t[3] >= _CROSS_SESSION_MIN_CACHED:
            by_copy.setdefault((r["model"], r["ts"]) + t, []).append(r)
    for group in by_copy.values():
        if len(group) > 1 and len({r["session_id"] for r in group}) > 1:
            merge(group)

    # Resolve chains (A -> B -> C) so every mark points at a billed row.
    def root(u):
        while u in marks:
            u = marks[u]
        return u
    resolved = {u: root(u) for u in marks}
    if apply:
        for u, keep in resolved.items():
            conn.execute("UPDATE messages SET dup_of=?, dedupe_method='heuristic' "
                         "WHERE uuid=? AND dup_of IS NULL", (keep, u))
        for keep, out in out_max.items():
            if keep in resolved:
                continue
            conn.execute("UPDATE messages SET output_tokens=?, "
                         "dedupe_method=COALESCE(dedupe_method, 'heuristic') "
                         "WHERE uuid=? AND output_tokens < ?", (out, keep, out))
        conn.commit()
    return {"rows_marked": len(resolved), "groups": len({v for v in resolved.values()})}


def estimate_gone_sessions(conn, cap_seconds: float, apply: bool) -> dict:
    """Busy time for sessions with no transcript-based activity, from the ledger.

    Turn starts are stored prompt texts that pass the human-prompt rule; a turn
    ends at its last billed row before the next prompt. Subagent rows share the
    parent's session id and are read in one sequence, so parallel work is
    under-counted: this is a floor, and it is stored as method 'ledger'.
    """
    on_disk = {os.path.basename(r[0])[:-len(".jsonl")] for r in conn.execute(
        "SELECT path FROM files") if "/subagents/" not in r[0] and os.path.exists(r[0])}
    sessions = [r[0] for r in conn.execute(
        "SELECT DISTINCT session_id FROM messages WHERE dup_of IS NULL AND model LIKE ?",
        ("claude-%",))]
    todo = [s for s in sessions if s and s not in on_disk]
    total = 0.0
    if apply:
        conn.execute("DELETE FROM agent_activity WHERE method='ledger'")
    for sid in todo:
        events = [(str(r["ts"]), "a") for r in conn.execute(
            "SELECT ts FROM messages WHERE session_id=? AND dup_of IS NULL AND ts <> ''",
            (sid,))]
        for r in conn.execute(
                "SELECT ts, text FROM message_text WHERE session_id=? AND role='user' "
                "AND text IS NOT NULL", (sid,)):
            text = activity.prompt_body(r["text"] or "").strip()
            if text and not text.startswith(activity._MACHINE_PREFIXES):
                ts = r["ts"]
                ts = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                events.append((ts, "p"))
        parsed = []
        for ts, kind in events:
            t = activity._parse_ts(ts.replace(" ", "T"))
            if t is not None:
                parsed.append((t, kind))
        parsed.sort()
        busy: dict = {}
        last = None
        in_turn = False
        for t, kind in parsed:
            if kind == "p":
                in_turn, last = True, t
                continue
            if in_turn and last is not None and t > last:
                start, end = last, min(t, last + timedelta(seconds=cap_seconds))
                while start < end:
                    midnight = (start + timedelta(days=1)).replace(hour=0, minute=0,
                                                                   second=0, microsecond=0)
                    stop = min(end, midnight)
                    day = start.date().isoformat()
                    busy[day] = busy.get(day, 0.0) + (stop - start).total_seconds()
                    start = stop
            last = t
        for day, sec in busy.items():
            total += sec
            if apply:
                conn.execute(
                    "INSERT INTO agent_activity (session_id, agent_id, prompt_id, day, method, "
                    "busy_seconds) VALUES (?, '', '', ?, 'ledger', ?)", (sid, day, sec))
    if apply:
        conn.commit()
    return {"sessions": len(todo), "agent_hours": round(total / 3600, 1)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args(argv)
    cfg = config.load(os.environ.get("TOKEN_AUDIT_CONFIG", "config.toml"))
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    db.connect(cfg["db_path"]).close()      # schema migration
    print("before:", _totals(conn))
    if not args.apply:
        print("heuristic collapse (dry run):", collapse_heuristic(conn, apply=False))
        print("re-scan and busy-time passes run only with --apply")
        return
    started = datetime.now()
    print("re-scan: new rows", rescan(conn, cfg["projects_dir"]))
    print("heuristic collapse:", collapse_heuristic(conn, apply=True))
    print("gone-session busy time:",
          estimate_gone_sessions(conn, activity.idle_cap_seconds(cfg), apply=True))
    print("after:", _totals(conn), f"({(datetime.now() - started).seconds}s)")


if __name__ == "__main__":
    main()
