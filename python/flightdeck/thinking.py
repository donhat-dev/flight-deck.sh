"""Thinking per API response: how long the model thought and how many tokens.

Claude Code stores no thinking text (a thinking block carries only its signature),
so this reads what the transcript does record: `usage.output_tokens_details.
thinking_tokens` (CLI versions from mid-August 2026) and the time each content
block was written. One row per response (message id, request id) in
`response_timing`:

- started_at: the last record written before the response's first line, i.e. when
  the request went out, after tool results and hook output were added;
- think_end_at: when the last thinking block was written;
- ended_at: when the last block was written.

think_end_at - started_at is the time to the end of thinking. It includes the wait
for the first token (queue and prefill), which grows with context size, so compare
it within one model and effort.
"""
import statistics
from collections import defaultdict
from datetime import datetime

# A longer gap means the start is not this request's (a resumed or copied history).
MAX_THINK_S = 1800


def zulu(dt):
    """A datetime as the transcript writes timestamps, so text comparison orders them."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _seconds(start, end):
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return (b - a).total_seconds()


def _has_thinking(obj):
    content = (obj.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking") for b in content)


_LATER = ("CASE WHEN excluded.{c} IS NOT NULL AND (response_timing.{c} IS NULL "
          "OR excluded.{c} {op} response_timing.{c}) THEN excluded.{c} ELSE response_timing.{c} END")


def record(conn, row, obj, started_at, agent_id):
    """Fold one billed line (ingest.parse_line `row`, raw `obj`) into its response.
    `started_at` is the timestamp of the record written just before this line."""
    if not row.get("message_id"):
        return
    usage = (obj.get("message") or {}).get("usage") or {}
    thinking_tokens = (usage.get("output_tokens_details") or {}).get("thinking_tokens")
    ts = row["ts"]
    conn.execute(
        "INSERT INTO response_timing (message_id, request_id, session_id, agent_id, model, "
        "effort, started_at, think_end_at, ended_at, thinking_tokens, output_tokens) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (message_id, request_id) DO UPDATE SET "
        # The first line's start is the earliest; a later line follows tool results.
        "started_at=" + _LATER.format(c="started_at", op="<") + ", "
        "think_end_at=" + _LATER.format(c="think_end_at", op=">") + ", "
        "ended_at=" + _LATER.format(c="ended_at", op=">") + ", "
        "thinking_tokens=" + _LATER.format(c="thinking_tokens", op=">") + ", "
        "output_tokens=" + _LATER.format(c="output_tokens", op=">") + ", "
        "effort=COALESCE(response_timing.effort, excluded.effort)",
        (row["message_id"], row["request_id"], row["session_id"], agent_id or "", row["model"],
         obj.get("perTurnEffort") or obj.get("effort"), started_at,
         ts if _has_thinking(obj) else None, ts, thinking_tokens, row["output_tokens"]))


def thought(r):
    """Whether a response thought. Opus 5.5 and Fable 5.1 return the notes between tool
    calls as empty thinking blocks with no thinking tokens, so a block alone is not
    enough; only lines written before thinking_tokens existed fall back to the block."""
    if r["thinking_tokens"] is not None:
        return r["thinking_tokens"] > 0
    return bool(r["think_end_at"])


def _stats(rows):
    thought_rows = [r for r in rows if thought(r)]
    secs = []
    for r in thought_rows:
        if r["think_end_at"] and r["started_at"]:
            s = _seconds(r["started_at"], r["think_end_at"])
            if 0 <= s <= MAX_THINK_S:
                secs.append(s)
    toks = [r["thinking_tokens"] for r in thought_rows if r["thinking_tokens"] is not None]
    q = statistics.quantiles(secs, n=10) if len(secs) >= 2 else [secs[0]] * 9 if secs else None
    return {
        "responses": len(rows), "thinking": len(thought_rows),
        "thinking_share": round(len(thought_rows) / len(rows), 3) if rows else 0.0,
        "avg_s": round(sum(secs) / len(secs), 2) if secs else None,
        "p50_s": round(statistics.median(secs), 2) if secs else None,
        "p90_s": round(q[8], 2) if q else None,
        "avg_tokens": round(sum(toks) / len(toks)) if toks else None,
        "timed": len(secs),
    }


def series(conn, since=None, model=None, effort=None, agent="all"):
    """Per UTC day: responses, the share that thought, and thinking time and tokens
    averaged over the responses that thought. `filters` lists what the range holds."""
    sql = ("SELECT model, effort, agent_id, started_at, think_end_at, thinking_tokens "
           "FROM response_timing WHERE model LIKE ? AND started_at IS NOT NULL")
    args = ["claude-%"]
    if since:
        sql += " AND started_at >= ?"
        args.append(since)
    rows = conn.execute(sql + " ORDER BY started_at", tuple(args)).fetchall()
    models, efforts = defaultdict(int), defaultdict(int)
    for r in rows:
        models[r["model"]] += 1
        efforts[r["effort"] or "unknown"] += 1
    picked = [r for r in rows
              if (not model or r["model"] == model)
              and (not effort or (r["effort"] or "unknown") == effort)
              and (agent == "all" or (agent == "main") == (not r["agent_id"]))]
    by_day = defaultdict(list)
    for r in picked:
        by_day[r["started_at"][:10]].append(r)
    return {
        "series": [{"date": d, **_stats(by_day[d])} for d in sorted(by_day)],
        "totals": _stats(picked),
        "filters": {"models": sorted(models, key=lambda m: -models[m]),
                    "efforts": sorted(efforts, key=lambda e: -efforts[e])},
    }
