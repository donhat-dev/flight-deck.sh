"""Quota burn rate: /usage meter readings joined with the ledger.

Every /usage poll is stored in `quota_readings`. The meters show whole percents,
so a fixed clock interval carries up to one point of error. `spans` measures from
one meter tick to a later one instead: the poll that first shows n and the poll
that first shows m bracket exactly m - n points, and the only error left is what
was used inside the poll gap before each of the two ticks. Ledger figures are
computed when read, so a later ledger or price fix reaches them too.
"""
import bisect
import json
import time
from collections import defaultdict
from datetime import datetime, timezone

from flightdeck import activity, pricing

# Anthropic limit changes that change what one point is worth: (start, end, note),
# UTC. Spans that overlap one are flagged, not dropped.
LIMIT_EVENTS = (
    ("2026-09-14T06:59:00.000Z", "2026-09-14T07:01:00.000Z",
     "Claude Code weekly limits: the 1.5x promotion ended; 1.25x base from here"),
    ("2026-09-22T00:00:00.000Z", "2026-09-23T00:00:00.000Z",
     "Opus 5.5 launch: Claude Code 5-hour limits +20% (time of day unknown)"),
)
# Quota weight of one list-price dollar of each model, relative to Opus 5.5, from
# the controlled tests and the history refit in docs/2026-09-23-quota-calibration.md.
# First matching prefix wins. Opus 4.8 was not measured; it shares Opus 5's list
# price and takes its weight. Sonnet and Haiku were not measured either and count
# at list price. Other vendors' models never reach the Claude quota.
MODEL_WEIGHTS = (
    ("claude-opus-5-5", 1.0),
    ("claude-opus-5", 0.5),
    ("claude-opus-4", 0.5),
    ("claude-fable-5-1", 2.3),
    ("claude-fable-5", 0.75),
    ("claude-", 1.0),
)
# The models the Claude Code model picker offers (2026-09-24). The burn table lists
# these, used in the range or not; the ledger's total still counts every model.
LISTED_MODELS = ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5")
# Dollars (Opus 5.5 rates) per meter point from the same calibration, used until
# the last BASE_DAYS hold at least BASE_MIN_POINTS of clean spans.
BASE_FALLBACK = {"session": 1.22, "week": 11.0}
BASE_MIN_POINTS = {"session": 20, "week": 4}
BASE_DAYS = 7
# The weekly meter moves about a point an hour, so one hour of whole-percent
# readings says little; it is read over six times the requested window.
METER_WINDOW_FACTOR = {"session": 1, "week": 6}
# The weekly meters move about ten times slower than the session meter.
MIN_POINTS = {"session": 10}
DEFAULT_MIN_POINTS = 2
COARSE_EDGE_S = 300
# /usage prints reset times to the minute, and may round differently per poll.
_RESET_SLACK_S = 120


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _epoch(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def record_reading(conn, rep, source="poll"):
    """Store one parsed /usage report (usage_poll.parse_usage schema)."""
    five, week = rep.get("five_hour") or {}, rep.get("seven_day") or {}
    models = [{"label": m.get("label"), "pct": m.get("used_percentage"),
               "reset_at": m.get("resets_at")} for m in rep.get("weekly_models") or []]
    conn.execute(
        "INSERT INTO quota_readings (ts, session_pct, session_reset_at, week_pct, "
        "week_reset_at, models, source) VALUES (?,?,?,?,?,?,?) ON CONFLICT(ts) DO NOTHING",
        (_iso(rep.get("captured_at") or time.time()), five.get("used_percentage"),
         five.get("resets_at"), week.get("used_percentage"), week.get("resets_at"),
         json.dumps(models), source))
    conn.commit()


def _series(conn, meter, since=None, until=None):
    """[(epoch, ts, pct, reset_at)] for one meter: 'session', 'week' or a model label."""
    sql, args = "SELECT ts, session_pct, session_reset_at, week_pct, week_reset_at, models " \
                "FROM quota_readings WHERE 1=1", []
    if since:
        sql += " AND ts >= ?"
        args.append(since)
    if until:
        sql += " AND ts < ?"
        args.append(until)
    out = []
    for r in conn.execute(sql + " ORDER BY ts", tuple(args)).fetchall():
        if meter == "session":
            pct, reset = r["session_pct"], r["session_reset_at"]
        elif meter == "week":
            pct, reset = r["week_pct"], r["week_reset_at"]
        else:
            m = next((m for m in json.loads(r["models"] or "[]")
                      if (m.get("label") or "").lower() == meter.lower()), None)
            pct, reset = (m["pct"], m.get("reset_at")) if m else (None, None)
        if pct is not None:
            out.append((_epoch(r["ts"]), r["ts"], float(pct), reset))
    return out


def _segments(series):
    """Split at window changes and at drops (a limit reset inside a window).
    Each segment is a list of ticks: (epoch, ts, pct, gap_s), where a tick is the
    first reading that shows a higher value than the reading before it."""
    segments, ticks, prev = [], [], None
    for t, ts, pct, reset in series:
        same_window = (prev is not None and pct >= prev[2]
                       and (reset is None or prev[3] is None
                            or abs(reset - prev[3]) <= _RESET_SLACK_S))
        if not same_window:
            if ticks:
                segments.append(ticks)
            ticks = []
        elif pct > prev[2]:
            ticks.append((t, ts, pct, t - prev[0]))
        prev = (t, ts, pct, reset)
    if ticks:
        segments.append(ticks)
    return segments


def _ledger(conn, start, end):
    rows = conn.execute(
        "SELECT ts, model, session_id, agent_id, input_tokens, cache_read, cache_create_5m, "
        "cache_create_1h, output_tokens, speed, inference_geo FROM messages "
        "WHERE dup_of IS NULL AND model LIKE ? AND ts >= ? AND ts < ? ORDER BY ts",
        ("claude-%", start, end)).fetchall()
    return [r["ts"] for r in rows], rows


def _measure(rows, idle_cap_s):
    cost, unpriced, by_model = 0.0, 0, defaultdict(float)
    tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}
    sessions, sub, last_seen, busy = set(), 0, {}, 0.0
    for r in rows:
        n = {k: int(r[c] or 0) for k, c in (("input", "input_tokens"), ("output", "output_tokens"),
                                             ("cache_read", "cache_read"),
                                             ("cache_write_5m", "cache_create_5m"),
                                             ("cache_write_1h", "cache_create_1h"))}
        for k, v in n.items():
            tok[k] += v
        c = pricing.message_cost(r["model"] or "", n["input"], n["cache_read"], n["cache_write_5m"],
                                 n["cache_write_1h"], n["output"], r["ts"], r["speed"],
                                 r["inference_geo"])
        if c is None:
            unpriced += 1
        else:
            cost += c
            by_model[r["model"]] += c
        sessions.add(r["session_id"])
        sub += 1 if r["agent_id"] else 0
        # Busy time from billed rows only: the gap between two responses of one
        # agent, capped like agent_activity. Tool runs inside the gap count.
        key, t = (r["session_id"], r["agent_id"] or ""), _epoch(r["ts"])
        if key in last_seen:
            busy += min(t - last_seen[key], idle_cap_s)
        last_seen[key] = t
    return {"cost": round(cost, 4), "cost_by_model": {m: round(v, 4) for m, v in by_model.items()},
            "tokens": tok, "requests": len(rows), "unpriced_requests": unpriced,
            "sessions": len(sessions), "subagent_share": round(sub / len(rows), 3) if rows else 0.0,
            "agent_minutes": round(busy / 60, 1)}


def spans(conn, meter="session", since=None, until=None, min_points=None, idle_cap_s=None):
    """Tick-to-tick spans of at least `min_points` points, each with ledger figures."""
    min_points = min_points or MIN_POINTS.get(meter, DEFAULT_MIN_POINTS)
    idle_cap_s = idle_cap_s or activity.idle_cap_seconds()
    series = _series(conn, meter, since, until)
    segments = _segments(series)
    cut = [(a, b) for seg in segments for a, b in _tile(seg, min_points)]
    out = []
    if cut:
        ts_list, rows = _ledger(conn, cut[0][0][1], cut[-1][1][1])
        for a, b in cut:
            lo, hi = bisect.bisect_left(ts_list, a[1]), bisect.bisect_left(ts_list, b[1])
            m = _measure(rows[lo:hi], idle_cap_s)
            points, wall = b[2] - a[2], b[0] - a[0]
            events = [note for s, e, note in LIMIT_EVENTS if s < b[1] and e > a[1]]
            flags = (["coarse_edge"] if max(a[3], b[3]) > COARSE_EDGE_S else []) \
                + (["no_ledger"] if not m["requests"] else []) \
                + (["limit_change"] if events else []) \
                + (["unpriced"] if m["unpriced_requests"] else [])
            out.append({"start": a[1], "end": b[1], "wall_s": round(wall),
                        "points": points, "points_per_hour": round(points / wall * 3600, 2),
                        "edge_gap_s": [round(a[3]), round(b[3])],
                        "cost_per_point": round(m["cost"] / points, 4), **m,
                        "limit_events": events, "flags": flags})
    clean = [s for s in out if not s["flags"]]
    pts, cost = sum(s["points"] for s in clean), sum(s["cost"] for s in clean)
    return {"meter": meter, "min_points": min_points, "readings": len(series),
            "last_reading": series[-1][1] if series else None, "spans": out,
            "summary": {"clean_spans": len(clean), "points": pts, "cost": round(cost, 4),
                        "cost_per_point": round(cost / pts, 4) if pts else None}}


def _tile(ticks, min_points):
    """Consecutive (start, end) tick pairs covering >= min_points each; the
    remainder at the end of a segment is left out."""
    i = 0
    for j in range(1, len(ticks)):
        if ticks[j][2] - ticks[i][2] >= min_points:
            yield ticks[i], ticks[j]
            i = j


def weight_of(model):
    for prefix, w in MODEL_WEIGHTS:
        if (model or "").startswith(prefix):
            return w
    return None


def _weighted(cost_by_model):
    return sum(c * (weight_of(m) or 0.0) for m, c in cost_by_model.items())


def base(conn, meter, now=None):
    """Dollars at Opus 5.5 rates per meter point: measured from the clean spans of
    the last BASE_DAYS when there are enough, else the calibrated value."""
    now = time.time() if now is None else now
    clean = [s for s in spans(conn, meter, since=_iso(now - BASE_DAYS * 86400))["spans"]
             if not s["flags"]]
    pts = sum(s["points"] for s in clean)
    measured = pts >= BASE_MIN_POINTS.get(meter, BASE_MIN_POINTS["week"])
    k = sum(_weighted(s["cost_by_model"]) for s in clean) / pts if measured else None
    return {"usd_per_point": round(k, 4) if k else BASE_FALLBACK.get(meter, BASE_FALLBACK["week"]),
            "source": "measured" if k else "calibrated", "spans": len(clean), "points": pts}


def step(conn, before, after, k):
    """Points used between two consecutive readings (epoch, ts, pct, reset_at).
    Inside one window it is the difference. Across a reset or a drop the old
    window's remainder is unknown: at least `after`, at most after + 100 - before
    (the old window used up), estimated from the ledger in between."""
    (t1, ts1, b, r1), (t2, ts2, a, r2) = before, after
    if a >= b and (r1 is None or r2 is None or abs(r2 - r1) <= _RESET_SLACK_S):
        return a - b, False
    rows = _ledger(conn, ts1, ts2)[1]
    used = _weighted(_measure(rows, activity.idle_cap_seconds())["cost_by_model"]) / k
    return min(max(used, a), a + 100 - b), True


def _meter_now(conn, meter, k, now, window_s):
    start = now - window_s
    series = _series(conn, meter, since=_iso(start - 3600))
    pre = [r for r in series if r[0] <= start]
    pts = pre[-1:] + [r for r in series if r[0] > start]
    total, resets = 0.0, 0
    for before, after in zip(pts, pts[1:]):
        used, crossed = step(conn, before, after, k)
        total, resets = total + used, resets + crossed
    covered = pts[-1][0] - pts[0][0] if len(pts) > 1 else 0
    return {"points": round(total, 2), "covered_s": round(covered), "resets": resets,
            "per_hour": round(total / covered * 3600, 2) if covered else None,
            "current": pts[-1][2] if pts else None, "reset_at": pts[-1][3] if pts else None}


def _per_model(conn, start, end):
    """{model: [tokens, list-price usd]} for Claude models between two ISO times."""
    out = defaultdict(lambda: [0, 0.0])
    for r in _ledger(conn, start, end)[1]:
        n = [int(r[c] or 0) for c in ("input_tokens", "cache_read", "cache_create_5m",
                                      "cache_create_1h", "output_tokens")]
        cost = pricing.message_cost(r["model"], *n, r["ts"], r["speed"], r["inference_geo"])
        if cost is not None:
            out[r["model"]][0] += sum(n)
            out[r["model"]][1] += cost
    return out


def stats(conn, since=None, window_s=3600, now=None):
    """Baseline per meter, the meters' burn over the last `window_s`, and per model:
    its weight, M tokens per meter point (token mix since `since`) and its current
    burn predicted from the ledger."""
    now = time.time() if now is None else now
    meters, recent = {}, {}
    for meter in ("session", "week"):
        b, span = base(conn, meter, now), window_s * METER_WINDOW_FACTOR[meter]
        meters[meter] = {"base": b, "window_s": span,
                         "now": _meter_now(conn, meter, b["usd_per_point"], now, span)}
        recent[meter] = _per_model(conn, _iso(now - span), _iso(now))
    mix = _per_model(conn, since or "0", _iso(now))

    def listed(per_model, prefix):
        rows = [v for m, v in per_model.items() if m.startswith(prefix)]
        return sum(t for t, _ in rows), sum(c for _, c in rows)

    models = []
    for prefix in LISTED_MODELS:
        w, (tokens, usd) = weight_of(prefix), listed(mix, prefix)
        now_usd = {m: listed(recent[m], prefix)[1] for m in meters}
        models.append({
            "model": prefix, "weight": w, "tokens": tokens, "usd": round(usd, 4),
            "now_usd": {m: round(v, 4) for m, v in now_usd.items()},
            # No use in the range means no token mix to convert with.
            "mtok_per_point": {m: round(tokens / (w * usd) * v["base"]["usd_per_point"] / 1e6, 3)
                               if usd > 0 else None for m, v in meters.items()},
            "now_per_hour": {m: round(w * now_usd[m] / v["base"]["usd_per_point"] * 3600
                                      / v["window_s"], 2) for m, v in meters.items()},
        })
    for m, v in meters.items():
        total = sum((weight_of(model) or 0.0) * c for model, (_, c) in recent[m].items())
        v["now"]["ledger_per_hour"] = round(
            total / v["base"]["usd_per_point"] * 3600 / v["window_s"], 2)
    return {"window_s": window_s, "meters": meters, "models": models}
