"""Tokens a phase actually used, priced two ways, and the meter change each predicts.

    python3 analyze2.py c     # or d (also works for a, b)

H1: the quota is API-equivalent cost at each model's list price ($1.22 per session
    point, $9.8 per weekly point, $2.6 per Fable-bucket point; from phases A+B and
    this week's /usage readings).
H0: same tokens priced as Opus 5.5, i.e. the published price gap is not applied.
"""
import glob, json, sys
sys.path.insert(0, "/home/nathando/Documents/Projects/flight-deck.sh/python")
from flightdeck import pricing

MARK = {"a": "Print exactly 2000 lines", "b": "Read tiny.txt 30 times",
        "c": "Run C.", "d": "Run D.", "e": "Run E."}
phase = sys.argv[1]
billed = {}
for f in glob.glob("/home/nathando/.claude/projects/-tmp-fd-quota-exp/*.jsonl"):
    lines = [json.loads(l) for l in open(f, errors="ignore") if l.strip()]
    first = next((o for o in lines if o.get("type") == "user"), None)
    text = json.dumps(first) if first else ""
    if MARK[phase] not in text or (phase == "a" and "Run C." in text):
        continue
    for o in lines:
        m = o.get("message") if o.get("type") == "assistant" else None
        if not isinstance(m, dict) or not m.get("usage"):
            continue
        u = m["usage"]; cc = u.get("cache_creation") or {}
        row = (m.get("model", ""), u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0),
               cc.get("ephemeral_5m_input_tokens", 0), cc.get("ephemeral_1h_input_tokens", 0),
               u.get("output_tokens", 0))
        key = (m.get("id"), o.get("requestId"))
        if key not in billed or row[5] > billed[key][5]:
            billed[key] = row
tot = {}
for r in billed.values():
    t = tot.setdefault(r[0], [0, 0, 0, 0, 0])
    for i in range(5):
        t[i] += r[i + 1]
h1 = h0 = 0.0
for model, t in tot.items():
    print(f"{model}: input {t[0]:,}  cache_read {t[1]:,}  cache_write_5m {t[2]:,}  cache_write_1h {t[3]:,}  output {t[4]:,}")
    h1 += pricing.message_cost(model, *t)
    h0 += pricing.message_cost("claude-opus-5-5", *t)
fable = sum(pricing.message_cost(m, *t) for m, t in tot.items() if m.startswith("claude-fable"))
print(f"\n{len(billed)} API calls | list-price cost ${h1:.2f} | priced as Opus 5.5 ${h0:.2f}")
print(f"{'':28} {'session +':>9} {'week +':>7} {'Fable +':>8}")
print(f"{'H1 list prices':28} {h1/1.22:9.1f} {h1/9.8:7.2f} {fable/2.6:8.1f}")
print(f"{'H0 priced as Opus 5.5':28} {h0/1.22:9.1f} {h0/9.8:7.2f} {'-':>8}")
