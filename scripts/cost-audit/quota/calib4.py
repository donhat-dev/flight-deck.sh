import json, bisect, collections
from datetime import datetime
import itertools
def solve(M, v):
    n = len(v); A = [row[:] + [v[i]] for i, row in enumerate(M)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(A[r][i]))
        if abs(A[p][i]) < 1e-12: return None
        A[i], A[p] = A[p], A[i]
        for r in range(n):
            if r != i:
                f = A[r][i] / A[i][i]
                A[r] = [a - f * b for a, b in zip(A[r], A[i])]
    return [A[i][n] / A[i][i] for i in range(n)]
def nnls(A, y):
    k = len(A[0]); best = ([0.0] * k, sum(v * v for v in y))
    for m in range(1, k + 1):
        for S in itertools.combinations(range(k), m):
            M = [[sum(r[i] * r[j] for r in A) for j in S] for i in S]
            v = [sum(r[i] * yy for r, yy in zip(A, y)) for i in S]
            x = solve(M, v)
            if x is None or min(x) < 0: continue
            coef = [0.0] * k
            for i, xi in zip(S, x): coef[i] = xi
            sse = sum((sum(a * b for a, b in zip(r, coef)) - yy) ** 2 for r, yy in zip(A, y))
            if sse < best[1]: best = (coef, sse)
    return best
from flightdeck import config, db, pricing
cfg = config.load("config.toml"); db.configure(cfg)
c = db.open_write(cfg["db_path"])
rows = c.execute("SELECT ts, model, input_tokens i, cache_read r, cache_create_5m w5, cache_create_1h w1, output_tokens o, speed, inference_geo g "
                 "FROM messages WHERE dup_of IS NULL AND model LIKE ? AND ts >= ? ORDER BY ts", ("claude-%", "2026-08-10")).fetchall()
def fam(m):
    return "fable" if m.startswith("claude-fable") else "opus55" if m.startswith("claude-opus-5-5") else "opus" if m.startswith("claude-opus") else "other"
FE = ["out", "in", "cw", "cr"]
ev = []
for r in rows:
    t = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
    ev.append((t, fam(r["model"]), {"out": int(r["o"] or 0), "in": int(r["i"] or 0), "cw": int(r["w5"] or 0) + int(r["w1"] or 0), "cr": int(r["r"] or 0),
        "cost": 1e6 * (pricing.message_cost(r["model"], int(r["i"] or 0), int(r["r"] or 0), int(r["w5"] or 0), int(r["w1"] or 0), int(r["o"] or 0), r["ts"], r["speed"], r["g"]) or 0.0)}))
ts = [e[0] for e in ev]
def vec(t0, t1):
    s = collections.Counter()
    for e in ev[bisect.bisect_left(ts, t0):bisect.bisect_right(ts, t1)]:
        for k, v in e[2].items(): s[(e[1], k)] += v
    return s
pts = []
rd = json.load(open("readings.json"))
rep = json.load(open("/home/nathando/.claude/token-audit/usage-report.json"))
print("live report captured_at", rep.get("captured_at"), rep["seven_day"])
for r in rd:
    t = datetime.fromisoformat(r["ts"]).timestamp(); reset = datetime.fromisoformat(r["week_reset"]).timestamp()
    if r["week"] < 5 or not (reset - 7*86400 <= t <= reset): continue
    pts.append((r["week_reset"][:10], r["week"], vec(reset - 7*86400, t), r.get("fable")))
fams = ["opus", "opus55", "fable", "other"]
def X(p, feats): return [sum(p[2][(f, k)] for f in fams) if k.startswith("*") is False and "/" not in k else 0 for k in feats]
def design(spec):
    return [[sum(p[2][key] for key in cols) / 1e6 for cols in spec] for p in pts]
y = [p[1] for p in pts]; weeks = [p[0] for p in pts]
def pred(A, coef): return [sum(a * b for a, b in zip(r, coef)) for r in A]
def mae(a, b): return sum(abs(x - z) for x, z in zip(a, b)) / len(a)
specs = {
 "cost all": [[(f, "cost") for f in fams]],
 "cost by family": [[(f, "cost")] for f in fams],
 "out only": [[(f, "out") for f in fams]],
 "out + cache_read": [[(f, "out") for f in fams], [(f, "cr") for f in fams]],
 "out + in+cw + cr": [[(f, "out") for f in fams], [(f, k) for f in fams for k in ("in", "cw")], [(f, "cr") for f in fams]],
 "out by family": [[(f, "out")] for f in fams],
 "out by family + cr": [[(f, "out")] for f in fams] + [[(f, "cr") for f in fams]],
}
for name, spec in specs.items():
    A = design(spec)
    coef, _ = nnls(A, y)
    errs = {}
    for w in sorted(set(weeks)):
        tr = [i for i, x in enumerate(weeks) if x != w]; te = [i for i, x in enumerate(weeks) if x == w]
        cw_, _ = nnls([A[i] for i in tr], [y[i] for i in tr])
        errs[w] = round(mae(pred([A[i] for i in te], cw_), [y[i] for i in te]), 1)
    print(f"{name:20} %/Mtok={[round(x, 4) for x in coef]} in-sample MAE={mae(pred(A, coef), y):.1f}pp LOWO={errs}")
