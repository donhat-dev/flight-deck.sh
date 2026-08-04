"""Derived metrics computed from the raw messages table + pricing."""
from datetime import datetime

from flightdeck import pricing, transcript

_DAYS_PER_MONTH = 30.4375


def _rows(conn, since=None):
    # `since` is an ISO-8601 date/datetime prefix; ts is ISO-8601 so a
    # lexicographic ">=" is also chronological. None => all rows.
    sql = ("SELECT model, ts, input_tokens, cache_read, cache_create_5m, "
           "cache_create_1h, output_tokens, session_id FROM messages")
    if since:
        return conn.execute(sql + " WHERE ts >= ?", (since,)).fetchall()
    return conn.execute(sql).fetchall()


# --- the rollup grain --------------------------------------------------------
# Every metric below used to pull each message row into Python and call
# `pricing.message_cost` once per row: 129,578 calls per metric per range, four
# ranges per snapshot. The SQL was never the cost (a full fetch measured 140ms);
# the per-row Python was — one `build_snapshot` took 4.1s against a 2.0s
# debounce, so an active session left FlightDeck rebuilding continuously.
#
# Aggregating to (day, model, session) collapses 129,578 rows to 628 and the
# pricing calls with them, a 206x cut, and one query then feeds all four metrics
# for all four ranges.
#
# The grain is only sound because a rate is uniform inside one (model, UTC day)
# cell: `pricing.rate_for` selects a promotion with `ts < until` where `until` is
# a bare DATE, so no rate boundary can fall mid-day. A promotion carrying a time
# would silently misprice half a cell —
# `test_the_promo_boundary_is_a_day_boundary` fails if one ever does.
#
# Costs are NOT stored, only tokens: cost is a function of (model, ts) and the
# pricing table is edited (a model added, a promotion ending). Deriving cost from
# tokens at read time means a pricing fix restates history correctly instead of
# leaving frozen numbers behind.
_GRAIN_SQL = (
    # COALESCE mirrors the old `(r["ts"] or "")[:10]`: a NULL ts belongs in the
    # "" bucket, not in a NULL one that would vanish from the daily chart.
    "SELECT COALESCE(substr(ts,1,10),'') AS day, model, session_id, "
    "COUNT(*) AS turns, "
    "SUM(input_tokens) AS input_tokens, SUM(cache_read) AS cache_read, "
    "SUM(cache_create_5m) AS cache_create_5m, "
    "SUM(cache_create_1h) AS cache_create_1h, "
    "SUM(output_tokens) AS output_tokens, "
    # RAW min/max, deliberately not NULLIF'd. '' sorts below every timestamp, so
    # these reproduce the old loop exactly: a session holding one empty ts keeps
    # '' as its first_ts. Callers that need a real date (summary's subscription
    # span) filter falsy values themselves, as the old loop's `if r["ts"]` did.
    "MIN(ts) AS first_ts, MAX(ts) AS last_ts "
    "FROM messages")


def rollup(conn, since=None) -> list[dict]:
    """Aggregate messages to the (day, model, session) grain, in SQL.

    Returns plain dicts so `slice_grain` can filter them without another query.
    """
    sql = _GRAIN_SQL
    params: tuple = ()
    if since:
        sql += " WHERE ts >= ?"
        params = (since,)
    sql += " GROUP BY 1, 2, 3"
    # int() on every sum is load-bearing, not defensive: PostgreSQL's SUM over a
    # bigint column returns numeric, which psycopg hands back as `Decimal`, and
    # `Decimal * float` raises TypeError inside pricing. The old code never hit it
    # because it read raw bigint columns. SQLite returns int, so a SQLite-only
    # test suite cannot catch this — it surfaced only against the real ledger.
    return [{
        "day": r["day"], "model": r["model"], "session_id": r["session_id"],
        "turns": int(r["turns"] or 0),
        "input_tokens": int(r["input_tokens"] or 0),
        "cache_read": int(r["cache_read"] or 0),
        "cache_create_5m": int(r["cache_create_5m"] or 0),
        "cache_create_1h": int(r["cache_create_1h"] or 0),
        "output_tokens": int(r["output_tokens"] or 0),
        "first_ts": r["first_ts"], "last_ts": r["last_ts"],
    } for r in conn.execute(sql, params).fetchall()]


def is_date_only(since) -> bool:
    """True for a bare `YYYY-MM-DD`, which is all `runtime.since()` ever returns."""
    return bool(since) and len(since) == 10 and since.count("-") == 2


def slice_grain(grain: list[dict], since=None) -> list[dict]:
    """Narrow a grain to a range without touching the database.

    The four snapshot ranges nest (today < 7d < 30d < all), so the widest grain
    contains every narrower one and re-querying per range is pure waste.

    Filtering whole cells is EXACT, not an approximation: every message in a cell
    shares the cell's date prefix, so `ts >= '2026-09-01'` is true for all of a
    cell's messages or none of them. A cell can never be half in range.

    That argument holds only while `since` is a bare date. A `since` carrying a
    time would split a day — `'2026-09-01' >= '2026-09-01T12:00'` is False, which
    would drop the whole day instead of half of it. Rather than return a quietly
    wrong number, this refuses; `_grain_for` then goes back to SQL.
    """
    if not since:
        return grain
    if not is_date_only(since):
        raise ValueError(
            f"since={since!r} carries a time; a (day, model, session) cell cannot "
            "be split by it — query the grain with this `since` instead")
    # `day` is "" for a NULL/empty ts, and "" >= any date is False, so those cells
    # drop out exactly as `WHERE ts >= ?` dropped their rows.
    return [c for c in grain if c["day"] >= since]


def _cell_cost(c: dict) -> float | None:
    """Cost of one grain cell, exact because the cell shares one rate."""
    return pricing.message_cost(
        c["model"], c["input_tokens"], c["cache_read"], c["cache_create_5m"],
        c["cache_create_1h"], c["output_tokens"], c["first_ts"])


def _cell_context(c: dict) -> int:
    return (c["input_tokens"] + c["cache_read"]
            + c["cache_create_5m"] + c["cache_create_1h"])


def _grain_for(conn, since, grain):
    """Use a caller-supplied grain when it can be sliced, else go back to SQL."""
    if grain is not None and (not since or is_date_only(since)):
        return slice_grain(grain, since)
    return rollup(conn, since)


def summary(conn, subscription_monthly_usd: float, rtk_savings_usd: float = 0.0,
            since=None, grain: list[dict] | None = None) -> dict:
    total_cost = 0.0
    cache_sav = 0.0
    tot_read = tot_create = tot_input = tot_output = 0
    unknown_tokens = 0
    msg_count = 0
    sessions = set()
    min_ts = max_ts = None
    for c_ in _grain_for(conn, since, grain):
        msg_count += c_["turns"]
        sessions.add(c_["session_id"])
        tot_read += c_["cache_read"]
        tot_create += c_["cache_create_5m"] + c_["cache_create_1h"]
        tot_input += c_["input_tokens"]
        tot_output += c_["output_tokens"]
        # Falsy first/last are skipped exactly as the row loop's `if r["ts"]` did:
        # '' is the grain's minimum, and letting it through would hand
        # `fromisoformat` an unparseable value and zero the subscription figure.
        if c_["first_ts"]:
            if min_ts is None or c_["first_ts"] < min_ts:
                min_ts = c_["first_ts"]
        if c_["last_ts"]:
            if max_ts is None or c_["last_ts"] > max_ts:
                max_ts = c_["last_ts"]
        cost = _cell_cost(c_)
        if cost is None:
            unknown_tokens += _cell_context(c_) + c_["output_tokens"]
        else:
            total_cost += cost
            cache_sav += pricing.cache_savings(
                c_["model"], c_["cache_read"], c_["first_ts"])
    denom = tot_read + tot_create + tot_input

    subscription_cost = 0.0
    if min_ts is not None and max_ts is not None:
        try:
            min_dt = datetime.fromisoformat(min_ts)
            max_dt = datetime.fromisoformat(max_ts)
            span_days = (max_dt - min_dt).days + 1
            months = span_days / _DAYS_PER_MONTH
            subscription_cost = subscription_monthly_usd * months
        except ValueError:
            subscription_cost = 0.0

    # "context" = full input-side tokens processed per turn
    # (uncached input + cache reads + cache writes). input_tokens alone is only
    # the uncached remainder, so it drastically understates real context size.
    total_context = denom
    return {
        "total_cost": total_cost,
        "cache_savings": cache_sav,
        "saved_vs_subscription": total_cost - subscription_cost,
        "rtk_savings": rtk_savings_usd,
        "cache_hit_rate": (tot_read / denom) if denom else 0.0,
        "input_tokens": tot_input,
        # raw input = full prompt size regardless of caching
        # (uncached + cache read + cache write); == total_context_tokens
        "input_tokens_raw": total_context,
        "output_tokens": tot_output,
        "session_count": len(sessions),
        "unknown_model_tokens": unknown_tokens,
        "message_count": msg_count,
        "total_context_tokens": total_context,
        "avg_context_per_turn": (total_context / msg_count) if msg_count else 0.0,
    }


def daily(conn, since=None, grain: list[dict] | None = None) -> list[dict]:
    out = {}
    for c_ in _grain_for(conn, since, grain):
        day = c_["day"]
        d = out.setdefault(day, {"date": day, "cost": 0.0, "input": 0,
                                 "output": 0, "cache_read": 0})
        d["input"] += c_["input_tokens"]
        d["output"] += c_["output_tokens"]
        d["cache_read"] += c_["cache_read"]
        # Unlike by_model, `<synthetic>` is NOT excluded here: it is a real turn
        # on the day's chart, just not a billable model.
        d["cost"] += _cell_cost(c_) or 0.0
    return [out[k] for k in sorted(out)]


def sessions(conn, limit: int = 100, offset: int = 0, since=None,
             projects_dir: str | None = None,
             grain: list[dict] | None = None) -> list[dict]:
    agg = {}
    for c_ in _grain_for(conn, since, grain):
        s = agg.setdefault(c_["session_id"], {
            "session_id": c_["session_id"], "project": None, "models": set(),
            "first_ts": None, "last_ts": None, "turns": 0, "input": 0,
            "output": 0, "cache_read": 0, "cache_create": 0, "cost": 0.0})
        s["models"].add(c_["model"])
        s["turns"] += c_["turns"]
        # Plain min/max over the RAW cell bounds, '' included. That is what the
        # row loop produced: '' is below every timestamp, so a session holding an
        # empty ts keeps '' as its first_ts and still takes a real last_ts.
        if s["first_ts"] is None or (c_["first_ts"] or "") < s["first_ts"]:
            s["first_ts"] = c_["first_ts"] or ""
        if s["last_ts"] is None or (c_["last_ts"] or "") > s["last_ts"]:
            s["last_ts"] = c_["last_ts"] or ""
        s["input"] += c_["input_tokens"]
        s["output"] += c_["output_tokens"]
        s["cache_read"] += c_["cache_read"]
        s["cache_create"] += c_["cache_create_5m"] + c_["cache_create_1h"]
        s["cost"] += _cell_cost(c_) or 0.0
    # project per session: fetch one representative cwd
    for row in conn.execute(
            "SELECT session_id, MAX(project) AS project FROM messages "
            "GROUP BY session_id"):
        if row["session_id"] in agg:
            agg[row["session_id"]]["project"] = row["project"]
    # session title from the ai-title records captured during ingest
    for s in agg.values():
        s["title"] = None
    for row in conn.execute("SELECT session_id, title FROM session_meta"):
        if row["session_id"] in agg:
            agg[row["session_id"]]["title"] = row["title"]
    # derive per-session context metrics
    for s in agg.values():
        # full input-side context read across the session (input + cache read + cache write)
        s["context"] = s["input"] + s["cache_read"] + s["cache_create"]
        s["input_raw"] = s["context"]   # alias: raw input total, cache or not
        s["avg_context"] = (s["context"] / s["turns"]) if s["turns"] else 0.0
        s["cache_ratio"] = (s["cache_read"] / s["context"]) if s["context"] else 0.0
    ordered = sorted(agg.values(), key=lambda x: x["last_ts"] or "", reverse=True)
    sliced = ordered[offset:offset + limit]
    for s in sliced:
        s["models"] = sorted(m for m in s["models"] if m)
    # attach subagent breakdown (a *subset* of the parent's totals — the ledger
    # already folds subagent usage into the parent session_id). Read on demand
    # from the transcript files; only sessions with a subagents/ dir cost I/O.
    if projects_dir:
        # ONE tree walk for the whole page instead of one per session: the
        # per-session recursive glob cost 202ms for 100 sessions against 3ms for
        # a single walk, and the snapshot did it four times over.
        by_session = transcript.subagent_files_by_session(projects_dir)
        for s in sliced:
            subs = []
            for u in transcript.subagent_usage(
                    projects_dir, s["session_id"],
                    files=by_session.get(s["session_id"], [])):
                cost = pricing.message_cost(
                    u["model"], u["input_tokens"], u["cache_read"],
                    u["cache_create_5m"], u["cache_create_1h"], u["output"],
                    # A subagent's usage is summed over its whole run, so it has no
                    # single message timestamp — its start is the right stamp for
                    # picking a dated rate.
                    u.get("first_ts"))
                subs.append({
                    "agent_id": u["agent_id"],
                    "agent_type": u["agent_type"],
                    "model": u["model"],
                    "turns": u["turns"],
                    "input_raw": (u["input_tokens"] + u["cache_read"]
                                  + u["cache_create_5m"] + u["cache_create_1h"]),
                    "output": u["output"],
                    "cache_read": u["cache_read"],
                    "cost": cost or 0.0,
                    "first_ts": u["first_ts"],
                    "last_ts": u["last_ts"],
                    "dispatch": u["dispatch"],
                })
            s["subagents"] = subs
    return sliced


# Claude Code injects "<synthetic>" turns (local command output, tool results
# with no real model). They are not a billable model, so they are excluded from
# the per-model breakdown.
SYNTHETIC_MODEL = "<synthetic>"


def by_model(conn, since=None, grain: list[dict] | None = None) -> list[dict]:
    agg = {}
    for c_ in _grain_for(conn, since, grain):
        if c_["model"] == SYNTHETIC_MODEL:
            continue
        m = agg.setdefault(c_["model"], {
            "model": c_["model"], "input": 0, "input_raw": 0, "output": 0,
            "cache_read": 0,
            "cost": 0.0, "priced": pricing.rate_for(c_["model"] or "") is not None})
        m["input"] += c_["input_tokens"]
        m["input_raw"] += _cell_context(c_)
        m["output"] += c_["output_tokens"]
        m["cache_read"] += c_["cache_read"]
        # One model can span a rate change, so its total is the sum of per-day
        # cells rather than one blended rate applied to the whole span.
        m["cost"] += _cell_cost(c_) or 0.0
    return sorted(agg.values(), key=lambda x: x["cost"], reverse=True)
