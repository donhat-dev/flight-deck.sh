exec(open("calib4.py").read().split("pts = []")[0])
from datetime import datetime, timezone
rep = json.load(open("/home/nathando/.claude/token-audit/usage-report.json"))
reset = rep["seven_day"]["resets_at"]; start = reset - 7 * 86400
fams = ["opus", "opus55", "fable", "other"]
coef = {"opus": 10.7134, "opus55": 4.0867, "fable": 35.2492, "other": 0.5066}
rd = [r for r in json.load(open("readings.json")) if r["week_reset"][:10] == "2026-09-29"]
pts = [(datetime.fromisoformat(r["ts"]).timestamp(), r["week"], r.get("fable")) for r in rd] + \
      [(rep["captured_at"], rep["seven_day"]["used_percentage"], rep["weekly_models"][0]["used_percentage"])]
for t, pct, fpct in sorted(pts):
    v = vec(start, t)
    out = {f: v[(f, "out")] for f in fams}
    pred = sum(coef[f] * out[f] / 1e6 for f in fams)
    tot = sum(out.values())
    print(datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d %H:%M"), f"week {pct:>4}% pred {pred:5.1f}%  fable {fpct}%  out(k) " +
          " ".join(f"{f}={out[f]//1000}" for f in fams), f" 1%≈{tot/pct/1000:.0f}k out" if pct else "")
