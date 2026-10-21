import json, bisect, collections, statistics, time
from datetime import datetime, timedelta, timezone
from flightdeck import config, db, pricing
cfg = config.load("config.toml"); db.configure(cfg)
c = db.open_write(cfg["db_path"])
rows = c.execute("SELECT ts, model, input_tokens i, cache_read r, cache_create_5m w5, cache_create_1h w1, output_tokens o, speed, inference_geo g "
                 "FROM messages WHERE dup_of IS NULL AND model LIKE ? AND ts >= ? ORDER BY ts", ("claude-%", "2026-08-10")).fetchall()
ev = []
for r in rows:
    a = (r["model"], int(r["i"] or 0), int(r["r"] or 0), int(r["w5"] or 0), int(r["w1"] or 0), int(r["o"] or 0))
    cost = pricing.message_cost(*a, r["ts"], r["speed"], r["g"]) or 0.0
    t = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
    fable = r["model"].startswith("claude-fable")
    ev.append((t, {"all": sum(a[1:]), "nocache": a[1] + a[3] + a[4] + a[5], "out": a[5], "cost": cost}, fable))
ts = [e[0] for e in ev]
def cum(t0, t1, fable_only=False):
    s = collections.Counter()
    for e in ev[bisect.bisect_left(ts, t0):bisect.bisect_right(ts, t1)]:
        if fable_only and not e[2]: continue
        s.update(e[1])
    return s
readings = json.load(open("readings.json"))
# the live file reading for this week
q = json.load(open("/home/nathando/.claude/token-audit/usage-report.json"))
print("report keys", list(q)[:8])
out = []
for rd in readings:
    t = datetime.fromisoformat(rd["ts"]).timestamp()
    reset = datetime.fromisoformat(rd["week_reset"]).timestamp()
    start = reset - 7 * 86400
    if t < start or t > reset: continue
    s = cum(start, t)
    rec = {"week": rd["week_reset"][:10], "ts": rd["ts"][:16], "pct": rd["week"], "elapsed_h": round((t - start) / 3600, 1), **s}
    if "fable" in rd:
        f = cum(start, t, True); rec["fable_pct"] = rd["fable"]; rec.update({"f_" + k: v for k, v in f.items()})
    out.append(rec)
json.dump(out, open("calib_points.json", "w"))
byw = collections.defaultdict(list)
for r in out: byw[r["week"]].append(r)
print(f"{'week':10} {'n':>3} {'maxpct':>6} | per 1%: {'cost$':>7} {'Mtok_all':>9} {'Mtok_nocache':>12} {'Mtok_out':>8} | spread cost (p10-p90)")
for w, rs in sorted(byw.items()):
    rs = [r for r in rs if r["pct"] >= 5]
    if not rs: print(w, "no readings >=5%"); continue
    def k(m): return sum(r[m] * r["pct"] for r in rs) / sum(r["pct"] ** 2 for r in rs)   # measure per 1%, LSQ through origin
    ratios = sorted(r["cost"] / r["pct"] for r in rs)
    p10, p90 = ratios[int(0.1 * (len(ratios) - 1))], ratios[int(0.9 * (len(ratios) - 1))]
    print(f"{w:10} {len(rs):>3} {max(r['pct'] for r in rs):>6} | {k('cost'):7.2f} {k('all')/1e6:9.1f} {k('nocache')/1e6:12.3f} {k('out')/1e6:8.3f} | {p10:.2f}-{p90:.2f}")
