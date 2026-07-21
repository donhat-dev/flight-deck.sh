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


def summary(conn, subscription_monthly_usd: float, rtk_savings_usd: float = 0.0,
            since=None) -> dict:
    total_cost = 0.0
    cache_sav = 0.0
    tot_read = tot_create = tot_input = tot_output = 0
    unknown_tokens = 0
    msg_count = 0
    sessions = set()
    min_ts = max_ts = None
    for r in _rows(conn, since):
        msg_count += 1
        sessions.add(r["session_id"])
        tot_read += r["cache_read"]
        tot_create += r["cache_create_5m"] + r["cache_create_1h"]
        tot_input += r["input_tokens"]
        tot_output += r["output_tokens"]
        if r["ts"]:
            if min_ts is None or r["ts"] < min_ts:
                min_ts = r["ts"]
            if max_ts is None or r["ts"] > max_ts:
                max_ts = r["ts"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        if c is None:
            unknown_tokens += (r["input_tokens"] + r["output_tokens"]
                                + r["cache_read"] + r["cache_create_5m"]
                                + r["cache_create_1h"])
        else:
            total_cost += c
            cache_sav += pricing.cache_savings(r["model"], r["cache_read"])
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


def daily(conn, since=None) -> list[dict]:
    out = {}
    for r in _rows(conn, since):
        day = (r["ts"] or "")[:10]
        d = out.setdefault(day, {"date": day, "cost": 0.0, "input": 0,
                                 "output": 0, "cache_read": 0})
        d["input"] += r["input_tokens"]
        d["output"] += r["output_tokens"]
        d["cache_read"] += r["cache_read"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        d["cost"] += c or 0.0
    return [out[k] for k in sorted(out)]


def sessions(conn, limit: int = 100, offset: int = 0, since=None,
             projects_dir: str | None = None) -> list[dict]:
    agg = {}
    for r in _rows(conn, since):
        s = agg.setdefault(r["session_id"], {
            "session_id": r["session_id"], "project": None, "models": set(),
            "first_ts": r["ts"], "last_ts": r["ts"], "turns": 0, "input": 0,
            "output": 0, "cache_read": 0, "cache_create": 0, "cost": 0.0})
        s["models"].add(r["model"])
        s["turns"] += 1
        if r["ts"] and (s["first_ts"] is None or r["ts"] < s["first_ts"]):
            s["first_ts"] = r["ts"]
        if r["ts"] and (s["last_ts"] is None or r["ts"] > s["last_ts"]):
            s["last_ts"] = r["ts"]
        s["input"] += r["input_tokens"]
        s["output"] += r["output_tokens"]
        s["cache_read"] += r["cache_read"]
        s["cache_create"] += r["cache_create_5m"] + r["cache_create_1h"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        s["cost"] += c or 0.0
    # project per session: fetch one representative cwd
    for row in conn.execute(
            "SELECT session_id, project FROM messages GROUP BY session_id"):
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
        for s in sliced:
            subs = []
            for u in transcript.subagent_usage(projects_dir, s["session_id"]):
                cost = pricing.message_cost(
                    u["model"], u["input_tokens"], u["cache_read"],
                    u["cache_create_5m"], u["cache_create_1h"], u["output"])
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


def by_model(conn, since=None) -> list[dict]:
    agg = {}
    for r in _rows(conn, since):
        if r["model"] == SYNTHETIC_MODEL:
            continue
        m = agg.setdefault(r["model"], {
            "model": r["model"], "input": 0, "input_raw": 0, "output": 0,
            "cache_read": 0,
            "cost": 0.0, "priced": pricing.rate_for(r["model"] or "") is not None})
        m["input"] += r["input_tokens"]
        m["input_raw"] += (r["input_tokens"] + r["cache_read"]
                           + r["cache_create_5m"] + r["cache_create_1h"])
        m["output"] += r["output_tokens"]
        m["cache_read"] += r["cache_read"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        m["cost"] += c or 0.0
    return sorted(agg.values(), key=lambda x: x["cost"], reverse=True)
