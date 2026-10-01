"""Prototype: tasks (human prompt -> end of agent work) with cost, and agent work-hours per day.
Read-only over ~/.claude/projects. Rules under test:
- A task starts at a user record whose turnOrigin is 'human' (or promptSource 'user'/'sdk' without turnOrigin).
  System-origin turns (task notifications, hooks) continue the current task.
- Subagent files (<session>/subagents/agent-*.jsonl) carry the parent turn's promptId on their records.
- Busy time per file = sum of gaps between consecutive records inside a turn, each gap capped at CAP seconds
  (a longer gap is treated as waiting for a human). Idle time between turns is not counted.
- Every model counts 1:1. API calls are deduped by (message.id, requestId), keeping the largest output."""
import glob, json, os, collections, statistics
from datetime import datetime, timedelta, timezone
CAP = int(os.environ.get("CAP", "900")); TZ = timezone(timedelta(hours=7)); SINCE = os.environ.get("SINCE", "2026-08-24")
P = {"claude-opus-5-5": (4, 5, 8, .20, 20), "claude-opus-5": (5, 6.25, 10, .5, 25), "claude-opus-4-8": (5, 6.25, 10, .5, 25),
     "claude-fable-5-1": (10, 12.5, 20, .25, 50), "claude-fable-5": (10, 12.5, 20, 1, 50),
     "claude-sonnet-5": (2, 2.5, 4, .2, 10), "claude-haiku-4-5": (1, 1.25, 2, .1, 5)}
def cost(model, t):
    k = max((p for p in P if (model or "").startswith(p)), key=len, default=None)
    if not k: return 0.0
    r = P[k]; return (t[0]*r[0] + t[1]*r[3] + t[2]*r[1] + t[3]*r[2] + t[4]*r[4]) / 1e6
ts_ = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
def is_human(o):
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"): return False
    if o.get("turnOrigin"): return o["turnOrigin"] == "human"
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c): return False
    return o.get("promptSource") in ("user", "sdk") or isinstance(c, str)
root = os.path.expanduser("~/.claude/projects")
calls = {}                     # (msg.id, reqId) -> (task_key, model, tokens)
task_meta = {}                 # task_key -> dict(start, session, text)
prompt_to_task = {}            # promptId -> task_key
busy = collections.defaultdict(float); busy_sub = collections.defaultdict(float)
task_busy = collections.defaultdict(float)
def add_busy(a, b, key, sub):
    # split [a, b) across local days
    while a < b:
        day_end = (a.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)
        e = min(b, day_end); d = a.astimezone(TZ).date().isoformat(); sec = (e - a).total_seconds()
        busy[d] += sec
        if sub: busy_sub[d] += sec
        if key: task_busy[key] += sec
        a = e
def scan(path, sub):
    recs = []
    for raw in open(path, "rb"):
        if not raw.endswith(b"\n"): break
        try: o = json.loads(raw.decode("utf-8", "replace"))
        except Exception: continue
        if o.get("timestamp") and o.get("type") in ("user", "assistant", "system"): recs.append(o)
    if not recs: return
    task = None
    if sub:
        pid = next((o.get("promptId") for o in recs if o.get("promptId")), None)
        task = prompt_to_task.get(pid)
    prev = None; in_turn = False
    for o in recs:
        t = ts_(o["timestamp"])
        if not sub and is_human(o):
            task = (o.get("sessionId"), o.get("promptId") or o.get("uuid"))
            c = (o.get("message") or {}).get("content")
            text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            task_meta.setdefault(task, dict(start=o["timestamp"], text=text[:120]))
            in_turn = True; prev = t
        elif not sub and o.get("type") == "user" and o.get("promptId") and task and o.get("promptId") not in prompt_to_task:
            in_turn = True; prev = t            # system-origin turn continues the task
        if not sub and o.get("promptId") and task: prompt_to_task.setdefault(o["promptId"], task)
        if sub: in_turn = True
        if in_turn and prev is not None and t > prev:
            add_busy(prev, min(t, prev + timedelta(seconds=CAP)), task, sub)
        prev = t
        m = o.get("message") or {}
        if o.get("type") == "assistant" and m.get("usage"):
            u = m["usage"]; cc = u.get("cache_creation") or {}
            tok = (u.get("input_tokens", 0) or 0, u.get("cache_read_input_tokens", 0) or 0, cc.get("ephemeral_5m_input_tokens", 0) or 0,
                   cc.get("ephemeral_1h_input_tokens", 0) or 0, u.get("output_tokens", 0) or 0)
            k = (m.get("id"), o.get("requestId"))
            if k not in calls or tok[4] > calls[k][2][4]: calls[k] = (task, m.get("model"), tok, o["timestamp"])
            if m.get("stop_reason") == "end_turn" and not sub: in_turn = False
files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
for f in sorted(files, key=lambda p: "/subagents/" in p):   # main files first, so promptIds are known for subagents
    scan(f, "/subagents/" in f)
task_cost = collections.defaultdict(float); unattributed = 0.0
for task, model, tok, ts in calls.values():
    if ts < SINCE: continue
    c = cost(model, tok)
    if task: task_cost[task] += c
    else: unattributed += c
tasks = [k for k, v in task_meta.items() if v["start"] >= SINCE]
costs = sorted(task_cost[k] for k in tasks); hours = sorted(task_busy[k] / 3600 for k in tasks)
print(f"CAP={CAP}s since={SINCE} tasks={len(tasks)} api_calls={sum(1 for v in calls.values() if v[3] >= SINCE):,}")
print(f"cost attributed to tasks ${sum(costs):,.0f}, unattributed ${unattributed:,.0f}")
q = lambda xs, p: xs[min(len(xs)-1, int(p*len(xs)))] if xs else 0
print(f"cost per task: median ${q(costs,.5):.2f} p90 ${q(costs,.9):.2f} max ${costs[-1] if costs else 0:.2f}")
print(f"agent-hours per task: median {q(hours,.5):.2f} p90 {q(hours,.9):.2f} max {hours[-1] if hours else 0:.2f}")
days = sorted(d for d in busy if d >= SINCE)
print("day        agent-h  of which subagent-h")
for d in days: print(f"{d}  {busy[d]/3600:7.2f}  {busy_sub[d]/3600:7.2f}")
print(f"mean agent-h/day over days with activity: {statistics.mean(busy[d] for d in days)/3600:.2f}")
top = sorted(tasks, key=lambda k: -task_cost[k])[:5]
for k in top: print(f"  ${task_cost[k]:7.2f}  {task_busy[k]/3600:5.2f}h  {task_meta[k]['start'][:16]}  {task_meta[k]['text'][:70]!r}")
