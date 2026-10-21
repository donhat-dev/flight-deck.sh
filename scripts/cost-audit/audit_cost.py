"""Audit FlightDeck's API-equivalent cost: pricing table and dedupe. Read-only on the ledger."""
import os, sys, collections, psycopg
sys.path.insert(0, "/home/nathando/Documents/Projects/flight-deck.sh/python")
from flightdeck import pricing as fd
# Corrected table, from platform.claude.com/docs/en/about-claude/pricing read 2026-09-23:
# (input, write5m, write1h, read, output). Sonnet 5 intro price became standard (no Sept 1 rise).
P = {"claude-opus-5-5": (4, 5, 8, .20, 20), "claude-opus-5": (5, 6.25, 10, .5, 25), "claude-opus-4-8": (5, 6.25, 10, .5, 25),
     "claude-fable-5-1": (10, 12.5, 20, .25, 50), "claude-fable-5": (10, 12.5, 20, 1, 50),
     "claude-sonnet-5": (2, 2.5, 4, .2, 10), "claude-haiku-4-5": (1, 1.25, 2, .1, 5)}
def rate(model):
    best = max((p for p in P if model.startswith(p)), key=len, default=None)
    return P.get(best)
def cost(model, t):
    r = rate(model)
    if r is None: return None
    return sum(a * b for a, b in zip(t, (r[0], r[3], r[1], r[2], r[4]))) / 1e6   # t = in, cr, cw5, cw1, out
def raw(model, t):   # same tokens with no caching: every input-side token at base input
    r = rate(model); return ((t[0] + t[1] + t[2] + t[3]) * r[0] + t[4] * r[4]) / 1e6
conn = psycopg.connect(os.environ["URL"]); conn.read_only = True
rows = conn.execute("""select session_id, model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens
                       from messages where model like 'claude-%%'""").fetchall()
def dedupe(rs):
    d = {}
    for s, m, ts, *t in rs:
        # Validated on the on-disk corpus against (message.id, requestId): within 0.3-1.4% per token class.
        k = (m, *t[:4]) if t[1] + t[2] + t[3] >= 10000 else (s, m, *t[:4])
        if k not in d or t[4] > d[k][3][4]: d[k] = (s, m, ts, t)
    return [(s, m, ts, *t) for s, m, ts, t in d.values()]
def report(label, rs):
    fd_cost = fd_sav = c = c_raw = old_sav = 0.0; n = 0; per = collections.defaultdict(float)
    for s, m, ts, *t in rs:
        n += 1
        x = fd.message_cost(m, t[0], t[1], t[2], t[3], t[4], ts); fd_cost += x or 0
        fd_sav += fd.cache_savings(m, t[1], ts)
        y = cost(m, t); c += y; c_raw += raw(m, t); per[m] += y
    print(f"{label:34s} rows={n:7,}  FlightDeck={fd_cost:9,.0f} (cache_savings {fd_sav:9,.0f})  corrected-prices={c:9,.0f}  raw-no-cache={c_raw:9,.0f}  net-savings={c_raw - c:9,.0f}")
    return per
for since in ("2000-01-01", "2026-08-24"):
    sub = [r for r in rows if (r[2] or "") >= since]
    print("== since", since)
    report("as stored (uuid rows)", sub)
    per = report("deduped (input-side key)", dedupe(sub))
    print("   per model, deduped + corrected:", {k: round(v) for k, v in sorted(per.items(), key=lambda x: -x[1])})
