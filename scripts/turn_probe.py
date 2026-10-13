"""Read-only probe: what a per-turn analysis can measure from the transcripts.

Takes Flightdoo's turns (byte ranges in each session's main transcript), reads
those bytes from the host copy of the files, and measures each turn: tool
calls, their real durations (call line -> result line), errors by class,
exact repeats, interruptions, steering messages, the model that answered.
Writes turns.jsonl and prints aggregates by model. Main agent only: the
tool calls inside subagent files are not counted.

Usage: python3 scripts/turn_probe.py [days]   (reads the DB through `docker exec pgcore-17`)
"""
import collections
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
# Outside the repository: the rows carry snippets of tool output.
OUT = os.environ.get("TURN_PROBE_OUT") or os.path.join(tempfile.gettempdir(), "turn_probe")
os.makedirs(OUT, exist_ok=True)

SQL = f"""
copy (select t.id, t.session_ref_id, t.started_at, t.ended_at, t.byte_start, t.byte_end,
             t.intent, t.kind, t.task_id, t.own_cost, t.duration, t.own_steers,
             s.follow_path, left(t.prompt_head, 80)
        from flightdeck_turn t join flightdeck_session s on s.id = t.session_ref_id
       where t.started_at > now() - interval '{DAYS} days' and s.follow_path is not null
       order by t.started_at) to stdout with csv
"""
rows = subprocess.run(
    ["docker", "exec", "pgcore-17", "sh", "-c",
     'psql -U "$POSTGRES_USER" -d flightdeck_odoo -At -c "%s"' % SQL.replace('"', '\\"')],
    capture_output=True, text=True, check=True).stdout
import csv
turns = list(csv.reader(rows.splitlines()))

# Error classes, most specific first. The text is the tool result's.
CLASSES = [
    ("user_denied", r"user doesn't want to proceed|The user denied|rejected by the user|user rejected"),
    ("classifier_denied", r"auto mode classifier|Permission for this action was denied"),
    ("hook_blocked", r"hook error|BLOCKED by|Blocked by|PreToolUse:.*(denied|block)"),
    ("permission", r"[Pp]ermission denied|EACCES|not allowed|requires approval"),
    ("input_invalid", r"InputValidationError|Invalid tool parameters|tool_use_error|is not a valid|must be one of"),
    ("not_read_first", r"has not been read yet|Read it first|must read"),
    ("edit_no_match", r"String to replace not found|old_string .*not found|Found \d+ matches"),
    ("file_missing", r"No such file|does not exist|ENOENT|not found: /|FileNotFoundError"),
    ("timeout", r"timed out|Timeout|TimeoutError|deadline exceeded"),
    ("interrupted", r"Request interrupted|interrupted by user|was aborted"),
    ("network", r"Connection refused|ECONNREFUSED|Name or service not known|getaddrinfo|Could not resolve host|502|503"),
    ("exit_nonzero", r"Exit code [1-9]\d*|exit status [1-9]|returned non-zero|Traceback|Error:"),
]
CLASS_RE = [(name, re.compile(rx)) for name, rx in CLASSES]


def classify(text):
    text = text or ""
    # grep, diff and test runners answer "nothing matched" with exit code 1.
    if re.match(r"Exit code 1\s*$", text.strip()) or (text.startswith("Exit code 1") and len(text) < 40):
        return "no_match"
    for name, rx in CLASS_RE:
        if rx.search(text):
            return name
    if "temporarily unavailable" in text or "Could not connect" in text or "page has been closed" in text:
        return "network"
    return "other"


# Who a failure is on. "failed" is a command that ran and failed: a real
# mistake, a script being iterated on, or an intended red test; rules cannot
# tell which, so it stays neutral and an analyzer judges it.
FAMILY = {
    "input_invalid": "avoidable", "edit_no_match": "avoidable", "not_read_first": "avoidable",
    "file_missing": "avoidable", "exit_nonzero": "failed", "other": "failed",
    "hook_blocked": "guard", "classifier_denied": "guard", "user_denied": "guard",
    "permission": "guard", "timeout": "environment", "network": "environment",
    "no_match": "expected", "interrupted": "person",
}


def ts_of(row):
    t = row.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def measure(path, start, end):
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            raw = fh.read((end - start) if end else -1)
    except OSError:
        return None
    calls, results, models = {}, {}, collections.Counter()
    first = last = prev = None
    active = 0.0
    texts = steers = interrupts = 0
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        t = ts_of(row)
        if t:
            first = first or t
            last = t
            if prev:
                active += min(max((t - prev).total_seconds(), 0), 300)
            prev = t
        a = row.get("attachment") or {}
        if a.get("type") == "queued_command":
            steers += 1
        msg = row.get("message") or {}
        content = msg.get("content")
        if row.get("type") == "assistant":
            if msg.get("model") and not msg["model"].startswith("<"):
                models[msg["model"]] += 1
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_use":
                    calls[b["id"]] = {"name": b.get("name"), "input": json.dumps(b.get("input"), sort_keys=True)[:400], "ts": t}
                elif b.get("type") == "text" and b.get("text", "").strip():
                    texts += 1
        elif row.get("type") == "user":
            if "[Request interrupted" in text_of(content):
                interrupts += 1
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = {
                        "error": bool(b.get("is_error")),
                        "text": text_of(b.get("content"))[:600],
                        "ts": t,
                    }
    out_calls = []
    seen = collections.Counter()
    for cid, c in calls.items():
        r = results.get(cid) or {}
        dur = (r["ts"] - c["ts"]).total_seconds() if r.get("ts") and c["ts"] else None
        key = (c["name"], c["input"])
        seen[key] += 1
        out_calls.append({
            "name": c["name"], "dur": dur, "error": r.get("error", False),
            "class": classify(r.get("text")) if r.get("error") else None,
            "err_text": (r.get("text") or "")[:200] if r.get("error") else None,
            "repeat": seen[key] > 1, "no_result": cid not in results,
        })
    return {
        "wall": (last - first).total_seconds() if first and last else 0,
        "active": active,
        "model": models.most_common(1)[0][0] if models else None,
        "texts": texts, "steers": steers, "interrupts": interrupts, "calls": out_calls,
    }


# A turn whose end was never written runs to the next turn's start, not to
# the end of the file (60 of 1,801 turns in 75 days had no end).
starts = collections.defaultdict(list)
for row in turns:
    starts[row[12]].append(int(row[4] or 0))
for v in starts.values():
    v.sort()


def end_of(path, start, end):
    if end:
        return end
    later = [b for b in starts[path] if b > start]
    return later[0] if later else 0


records = []
for (tid, sid, started, ended, bstart, bend, intent, kind, task_id, cost, duration, own_steers,
     path, head) in turns:
    host = path.replace("/mnt/claude-projects", os.path.expanduser("~/.claude/projects"))
    m = measure(host, int(bstart or 0), end_of(path, int(bstart or 0), int(bend or 0)))
    if not m:
        continue
    m.update({"turn": int(tid), "session": int(sid), "started": started, "intent": intent,
              "kind": kind, "cost": float(cost or 0), "head": head})
    records.append(m)

with open(os.path.join(OUT, "turns.jsonl"), "w") as fh:
    for r in records:
        fh.write(json.dumps(r, default=str) + "\n")

print(f"turns read: {len(records)} of {len(turns)} in the last {DAYS} days")
work = [r for r in records if r["calls"] and r["intent"] in ("task", "follow_up", "ask")]
by_model = collections.defaultdict(list)
for r in work:
    by_model[r["model"] or "(none)"].append(r)
hdr = f"{'model':26} {'turns':>5} {'calls p50':>9} {'p90':>4} {'avoidable':>9} {'guard':>6} {'env':>5} {'repeat':>6} {'active min p50':>14} {'tool share':>10}"
print(hdr)
for model, rs in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
    if len(rs) < 8:
        continue
    calls = [c for r in rs for c in r["calls"]]
    fam = collections.Counter(FAMILY[c["class"]] for c in calls if c["error"])
    n = sorted(len(r["calls"]) for r in rs)
    act = sorted(r["active"] / 60 for r in rs)
    q = lambda v, x: v[min(len(v) - 1, int(x * len(v)))]
    tool = sum(min(c["dur"] or 0, 300) for c in calls)
    print(f"{model:26} {len(rs):5} {q(n,.5):9} {q(n,.9):4} {fam['avoidable']/len(calls):9.2%} {fam['guard']/len(calls):6.1%} "
          f"{fam['environment']/len(calls):5.1%} {sum(c['repeat'] for c in calls)/len(calls):6.1%} "
          f"{q(act,.5):14.1f} {tool/max(sum(r['active'] for r in rs),1):10.0%}")

calls = [c for r in work for c in r["calls"]]
errs = [c for c in calls if c["error"]]
print("\nerror classes (work turns):", collections.Counter(c["class"] for c in errs).most_common())
print("avoidable errors by tool:", collections.Counter(c["name"] for c in errs if FAMILY[c["class"]] == "avoidable").most_common(8))
print("turns with a repeat of 3+ of the same call:", sum(1 for r in work if collections.Counter((c["name"],) for c in r["calls"] if c["repeat"]).most_common(1) and max(collections.Counter(c["name"] for c in r["calls"] if c["repeat"]).values()) >= 3), "of", len(work))
print("interrupts:", sum(r["interrupts"] for r in work), "steers:", sum(r["steers"] for r in work))
