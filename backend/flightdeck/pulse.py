"""FlightDeck Pulse - read the Claude Code background-agent store and project it
onto the attention-first lane model.

Source of truth (verified against Claude Code 2.1.208): background agents managed
by `claude agents` persist a per-session directory at

    ~/.claude/jobs/<SHORT_ID>/
        state.json      live snapshot (state, needs, intent, inFlight, tokens, ...)
        timeline.jsonl  append-only {at,state,detail,text} per transition
        tmp/            per-session scratch

We read those files directly (no CLI, no daemon socket, no Agent SDK), which keeps
this a pure filesystem poll that drops onto the same poll -> serve model the rest
of FlightDeck uses. If the jobs dir is absent (no bg agents ever started, or not
mounted into the container) the snapshot degrades honestly to "unavailable".
"""
import json
import os
import time

# state (from state.json / `claude agents --json`) -> board lane.
# Observed live: working, blocked. Others per the agent-view docs.
STATE_LANE = {
    "working": "flight",
    "busy": "flight",
    "blocked": "needs",       # waiting on a human question or permission
    "needs_input": "needs",
    "failed": "needs",        # failed sits in Needs Me with a fail flag
    "error": "needs",
    "review": "review",
    "review_ready": "review",
    "ready_for_review": "review",
    "stopped": "parked",
    "idle": "parked",         # ready for next prompt; TBD whether this is Needs Me
    "cancelled": "parked",
    "completed": "done",
    "done": "done",
    "finished": "done",
}
LANES = ("needs", "flight", "review", "parked", "done")

# States that legitimately have no live worker (the session ended on its own).
# A NON-terminal state with no worker in the roster = crashed / killed = stale.
TERMINAL_STATES = {"completed", "done", "finished", "stopped", "cancelled",
                   "failed", "error"}


def _model_from_flags(flags):
    """Pull the model out of respawnFlags like ['-n','x','--model','sonnet']."""
    if isinstance(flags, list):
        for i, f in enumerate(flags):
            if f == "--model" and i + 1 < len(flags):
                return flags[i + 1]
    return None


def _read_timeline(path, limit=80):
    out = []
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return out
    return out[-limit:]


def _project_of(cwd):
    if not cwd:
        return ""
    base = os.path.basename(cwd.rstrip("/"))
    return base or cwd


def read_job(job_dir):
    """Normalize one ~/.claude/jobs/<short>/ into a Pulse session dict, or None."""
    state_path = os.path.join(job_dir, "state.json")
    if not os.path.isfile(state_path):
        return None
    try:
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None

    short = os.path.basename(job_dir.rstrip("/"))
    state = (st.get("state") or "working").lower()
    lane = STATE_LANE.get(state, "flight")
    in_flight = st.get("inFlight") or {}
    children = st.get("children")
    child_count = len(children) if isinstance(children, list) else 0

    return {
        "id": short,
        "sessionId": st.get("sessionId"),
        "name": st.get("name") or short,
        "nameSource": st.get("nameSource"),
        "project": _project_of(st.get("cwd") or ""),
        "cwd": st.get("cwd") or "",
        "state": state,
        "lane": lane,
        "needs": st.get("needs") or st.get("detail"),
        "intent": st.get("intent"),
        "tempo": st.get("tempo"),
        "inFlight": {
            "tasks": in_flight.get("tasks", 0),
            "queued": in_flight.get("queued", 0),
            "kinds": in_flight.get("kinds", []),
        },
        "children": children,
        "childCount": child_count,
        "tokens": st.get("tokens"),
        "output": st.get("output"),
        "model": _model_from_flags(st.get("respawnFlags")),
        "backend": st.get("backend"),
        "cliVersion": st.get("cliVersion"),
        "createdAt": st.get("createdAt"),
        "updatedAt": st.get("updatedAt"),
        "firstTerminalAt": st.get("firstTerminalAt"),
        "timeline": _read_timeline(os.path.join(job_dir, "timeline.jsonl")),
    }


def roster_workers(daemon_dir=None):
    """Live worker registry from ~/.claude/daemon/roster.json (the supervisor's
    record of running background workers, keyed by short id). Returns a dict
    (possibly empty) when the roster is readable, or None when liveness cannot be
    determined (roster absent / not mounted) so callers can degrade honestly."""
    daemon_dir = os.path.expanduser(
        daemon_dir or os.environ.get("TOKEN_AUDIT_DAEMON_DIR") or "~/.claude/daemon")
    path = os.path.join(daemon_dir, "roster.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return None
    workers = r.get("workers") or {}
    return {
        short: {
            "pid": w.get("pid"),
            "cliVersion": w.get("cliVersion"),
            "startedAt": w.get("startedAt"),
        }
        for short, w in workers.items()
    }


def snapshot(jobs_dir=None, daemon_dir=None):
    """Build the whole Pulse board from the background-agent jobs store, enriched
    with supervisor liveness so a crashed/stale agent is not shown as live."""
    jobs_dir = os.path.expanduser(
        jobs_dir or os.environ.get("TOKEN_AUDIT_JOBS_DIR") or "~/.claude/jobs")
    available = os.path.isdir(jobs_dir)

    sessions = []
    if available:
        try:
            names = os.listdir(jobs_dir)
        except OSError:
            names = []
        for name in names:
            if name.startswith("."):        # .draft-* control files
                continue
            d = os.path.join(jobs_dir, name)
            if not os.path.isdir(d):         # pins.json etc.
                continue
            try:
                job = read_job(d)
            except Exception:
                job = None
            if job:
                sessions.append(job)

    # Liveness enrichment (ADDITIVE ONLY in V1). A session whose short id is in
    # the supervisor roster has a live worker right now -> we confirm it with an
    # "alive" flag + pid. We deliberately do NOT demote roster-absent sessions to
    # Parked/Stale: the transient daemon idle-exits and drops a blocked-but-
    # attachable worker from the roster, so absence != crashed. Reliable stale
    # detection needs temporal hysteresis (the roadmap "3 missed polls"), which
    # is stateful and out of scope for this stateless snapshot. So the lane stays
    # driven by state.json; liveness only ADDS a positive confirmation.
    raw = roster_workers(daemon_dir)
    workers = raw or {}
    liveness = raw is not None
    for s in sessions:
        short = s["id"]
        if liveness and short in workers:
            s["alive"] = True
            s["pid"] = workers[short]["pid"]
        else:
            s["alive"] = None
            s["pid"] = None
        s["stale"] = False   # reserved for a future temporal-hysteresis pass

    lanes = {k: [] for k in LANES}
    for s in sessions:
        lanes[s["lane"]].append(s)
    # newest activity first within each lane
    for k in LANES:
        lanes[k].sort(key=lambda s: s.get("updatedAt") or "", reverse=True)

    return {
        "available": available,
        "jobsDir": jobs_dir,
        "liveness": liveness,
        "daemonWorkers": len(workers) if liveness else None,
        "counts": {k: len(v) for k, v in lanes.items()},
        "attention": len(lanes["needs"]),
        "total": len(sessions),
        "lanes": lanes,
        "ts": time.time(),
    }
