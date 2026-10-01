"""Sum what a phase actually used and predict the quota change.

    python3 analyze.py a      # or b

Reads the runs' own transcripts (~/.claude/projects/-tmp-fd-quota-exp/), one
billed unit per (message.id, requestId) with the largest output, and tells
the phases apart by their prompt. The `-p` JSON outputs in out/ are only
checked for errors.
"""
import glob, json, sys

PHASE_MARK = {"a": "Print exactly 2000 lines", "b": "Read tiny.txt 30 times"}
phase = sys.argv[1]
billed = {}
sessions = 0
for f in glob.glob("/home/nathando/.claude/projects/-tmp-fd-quota-exp/*.jsonl"):
    lines = [json.loads(l) for l in open(f, errors="ignore") if l.strip()]
    first_user = next((o for o in lines if o.get("type") == "user"), None)
    if not first_user or PHASE_MARK[phase] not in json.dumps(first_user):
        continue
    sessions += 1
    for o in lines:
        m = o.get("message") if o.get("type") == "assistant" else None
        if not isinstance(m, dict) or not m.get("usage"):
            continue
        key = (m.get("id"), o.get("requestId"))
        u = m["usage"]
        row = {"model": m.get("model", ""), "input": u.get("input_tokens", 0),
               "cache_write": u.get("cache_creation_input_tokens", 0),
               "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)}
        if key not in billed or row["output"] > billed[key]["output"]:
            billed[key] = row

tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
other_out = 0
for r in billed.values():
    if "opus-5-5" in r["model"]:
        for k in tot:
            tot[k] += r[k]
    else:
        other_out += r["output"]
errors = [f for f in glob.glob(f"/tmp/fd-quota-exp/out/{phase}_*.json")
          if '"is_error":true' in open(f).read().replace(" ", "")]
print(f"phase {phase}: {sessions} runs, {len(billed)} API calls, errors in {len(errors)} runs")
print("opus 5.5 tokens:", {k: f"{v:,}" for k, v in tot.items()}, "| other-model output", f"{other_out:,}")

SESSION_PER_WEEK = 8.06          # 100% of a 5h window = 12.4% of the week (22 windows)
print(f"\n{'opus55 %/Mtok out':>18} {'cache-read %/Mtok':>18} | {'week +%':>8} {'session +%':>11}")
for k_out in (4.09, 7.0):
    for k_cr in (0.0, 0.005, 0.04):
        week = tot["output"] / 1e6 * k_out + tot["cache_read"] / 1e6 * k_cr + other_out / 1e6 * 0.51
        print(f"{k_out:>18} {k_cr:>18} | {week:8.2f} {week * SESSION_PER_WEEK:11.1f}")
