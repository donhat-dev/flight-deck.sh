"""Thinking time and tokens per response, by model and effort, from response_timing.

    PYTHONPATH=python .venv/bin/python scripts/cost-audit/thinking/matrix.py [since]

Only responses that thought. Time = request sent -> thinking block written; net
time subtracts the per-model floor (median time of responses with < 50 thinking
tokens, i.e. the wait for the first token) in the same context-size band. Per turn
= all responses of one human prompt (messages.prompt_id), main agent and subagents.
"""
import statistics
import sys
from collections import defaultdict

from flightdeck import config, db, thinking

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
MIN_N = 100
Q = ("min", "p10", "p25", "p50", "mean", "p75", "p90", "p95", "p99", "max")


def band(ctx):
    return "<150k" if ctx < 150e3 else "150-400k" if ctx < 400e3 else ">=400k"


def summary(v):
    v = sorted(v)
    q = statistics.quantiles(v, n=100, method="inclusive")
    return {"n": len(v), "min": v[0], "p10": q[9], "p25": q[24], "p50": q[49], "mean": sum(v) / len(v),
            "p75": q[74], "p90": q[89], "p95": q[94], "p99": q[98], "max": v[-1]}


cfg = config.load("python/config.toml")
db.configure(cfg)
c = db.open_write(cfg["db_path"])
rows = c.execute(
    "SELECT t.model, t.effort, t.started_at, t.think_end_at, t.thinking_tokens, t.output_tokens, "
    "m.prompt_id, m.input_tokens + m.cache_read + m.cache_create_5m + m.cache_create_1h AS ctx "
    "FROM response_timing t JOIN messages m ON m.message_id = t.message_id "
    "AND m.request_id = t.request_id AND m.dup_of IS NULL "
    "WHERE t.started_at >= ? AND t.model LIKE ?", (SINCE, "claude-%")).fetchall()

floor = defaultdict(list)
resp = []
for r in rows:
    s = thinking._seconds(r["started_at"], r["think_end_at"]) if r["think_end_at"] else None
    ok = s is not None and 0 <= s <= thinking.MAX_THINK_S
    rec = {**dict(r), "s": s if ok else None, "band": band(r["ctx"] or 0)}
    resp.append(rec)
    if ok and (r["thinking_tokens"] or 0) < 50:
        floor[(r["model"], rec["band"])].append(s)
        floor[(r["model"], "*")].append(s)
floor_s = {k: statistics.median(v) for k, v in floor.items() if len(v) >= 20}

groups = defaultdict(lambda: {"s": [], "net": [], "tok": [], "all": 0, "speed": []})
turns = defaultdict(lambda: defaultdict(lambda: {"think": 0, "out": 0, "n": 0}))
for r in resp:
    key = (r["model"], r["effort"] or "unknown")
    g = groups[key]
    g["all"] += 1
    if r["prompt_id"]:
        t = turns[key][r["prompt_id"]]
        t["think"] += r["thinking_tokens"] or 0
        t["out"] += r["output_tokens"] or 0
        t["n"] += 1
    if not thinking.thought(r):
        continue
    if r["thinking_tokens"] is not None:
        g["tok"].append(r["thinking_tokens"])
    if r["s"] is not None:
        g["s"].append(r["s"])
        f = floor_s.get((r["model"], r["band"]), floor_s.get((r["model"], "*")))
        if f is not None:
            net = max(0.0, r["s"] - f)
            g["net"].append(net)
            if (r["thinking_tokens"] or 0) >= 300 and net > 0:
                g["speed"].append(r["thinking_tokens"] / net)

keys = [k for k in sorted(groups, key=lambda k: -len(groups[k]["s"])) if len(groups[k]["s"]) >= MIN_N]
for title, field, d in (("SECONDS to end of thinking", "s", 1),
                        ("NET SECONDS (minus first-token floor)", "net", 1),
                        ("THINKING TOKENS per response", "tok", 0)):
    print(f"\n{title} (responses that thought, since {SINCE})")
    print(f"{'model':18} {'effort':7} {'n':>6} " + " ".join(f"{q:>7}" for q in Q))
    for k in keys:
        v = groups[k][field]
        if len(v) < 2:
            continue
        s = summary(v)
        print(f"{k[0]:18} {k[1]:7} {s['n']:6} " + " ".join(f"{float(s[q]):>7.{d}f}" for q in Q))
print("\nfloor (median s, <50 thinking tokens):",
      {f"{m}|{b}": round(v, 1) for (m, b), v in sorted(floor_s.items())})
print(f"\n{'model':18} {'effort':7} {'thought':>8} {'tok/s':>6} | per turn: {'turns':>5} {'resp p50':>8} "
      f"{'think p50':>9} {'think p90':>9} {'think mean':>10} {'out p50':>8} {'out mean':>9}")
for k in keys:
    g = groups[k]
    tv = list(turns[k].values())
    if not tv:
        continue
    th = sorted(t["think"] for t in tv)
    ou = sorted(t["out"] for t in tv)
    nn = sorted(t["n"] for t in tv)
    q90 = statistics.quantiles(th, n=10)[8] if len(th) >= 2 else th[0]
    speed = statistics.median(g["speed"]) if g["speed"] else float("nan")
    print(f"{k[0]:18} {k[1]:7} {len(g['s']) / g['all']:8.0%} {speed:6.0f} | {len(tv):15} "
          f"{statistics.median(nn):8.0f} {statistics.median(th):9.0f} {q90:9.0f} {sum(th) / len(th):10.0f} "
          f"{statistics.median(ou):8.0f} {sum(ou) / len(ou):9.0f}")
