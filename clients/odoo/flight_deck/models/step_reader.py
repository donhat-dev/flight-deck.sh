"""Read one turn's tool calls out of a transcript, and sort their failures.

Free of Odoo so the rules can be run and tested on their own. A turn is a byte
range of a session's main transcript; the subagents it started live in
`<session>/subagents/agent-<id>.jsonl`, found through the `agentId` each Agent
call's result carries, and are read whole.

Two things here are a ruler across model releases, so they change only with
RULES_VERSION: which result text falls in which class, and which class in
which family. Change either and every stored step says which rules made it.
"""
import collections
import hashlib
import json
import os
import re
from datetime import datetime

RULES_VERSION = 3
# Nested agents are rare past two levels; a cycle would be a bug in the files.
MAX_DEPTH = 3

# Most specific first; the first match wins. The text is the tool result's.
CLASSES = [
    ("user_denied", r"user doesn't want to|The user denied|rejected by the user|user rejected"),
    ("classifier_denied", r"auto mode classifier|Permission for this action was denied"),
    ("hook_blocked", r"hook error|BLOCKED by|Blocked by|Blocked:"),
    ("permission", r"[Pp]ermission denied|EACCES|requires approval"),
    # Before input_invalid: the CLI wraps every tool error in <tool_use_error>, so
    # the wrapper says nothing about the kind.
    ("not_read_first", r"has not been read yet|Read it first"),
    ("stale_read", r"has been modified since read"),
    ("edit_no_match", r"String to replace not found|Found \d+ matches of the string"),
    ("input_invalid", r"InputValidationError|Invalid tool parameters|Input validation error|"
                      r"is not a valid|must be one of|has not been loaded|No such tool available"),
    ("unknown_task", r"No task found with ID|No (?:background )?task .*found"),
    ("too_large", r"exceeds maximum allowed tokens|too large to read|File content .* exceeds"),
    ("file_missing", r"No such file|does not exist|ENOENT|FileNotFoundError"),
    ("timeout", r"timed out|Timeout \d+ms exceeded|TimeoutError|deadline exceeded"),
    ("interrupted", r"Request interrupted|interrupted by user|was aborted"),
    ("unavailable", r"Connection refused|ECONNREFUSED|Name or service not known|getaddrinfo|"
                    r"Could not resolve host|temporarily unavailable|Could not connect|page has been closed"),
    ("failed", r"Exit code [1-9]\d*|exit status [1-9]|returned non-zero|Traceback|Error"),
]
CLASS_RE = [(name, re.compile(rx)) for name, rx in CLASSES]

# Who a failure is on. "failed" is a command that ran and failed: a mistake, a
# script being iterated on, or an intended red test. Rules cannot tell which,
# so it stays neutral; a review judges it.
FAMILY = {
    "no_match": "expected",
    "user_denied": "guard", "classifier_denied": "guard", "hook_blocked": "guard", "permission": "guard",
    "input_invalid": "avoidable", "not_read_first": "avoidable", "edit_no_match": "avoidable",
    "stale_read": "avoidable", "unknown_task": "avoidable",
    "too_large": "avoidable", "file_missing": "avoidable",
    "timeout": "environment", "unavailable": "environment",
    "interrupted": "person",
    "failed": "failed", "other": "failed",
}
FAMILIES = ["avoidable", "failed", "guard", "environment", "expected", "person"]

AGENT_TOOLS = ("Agent", "Task")


def classify(text):
    """(class, family) for a result the CLI marked as an error."""
    text = (text or "").strip()
    # grep, diff and test runners say "nothing matched" with exit code 1.
    if text.startswith("Exit code 1") and len(text) < 40:
        return "no_match", "expected"
    for name, rx in CLASS_RE:
        if rx.search(text):
            return name, FAMILY[name]
    return "other", FAMILY["other"]


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def read_rows(path, start=0, end=0):
    """The JSON lines of a file, or of the byte range [start, end)."""
    with open(path, "rb") as fh:
        fh.seek(start)
        raw = fh.read((end - start) if end else -1)
    rows = []
    for line in raw.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def split_tool(name):
    """('mcp_server', 'tool') for an MCP tool, ('', name) for a built-in one."""
    if name and name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "", name or ""


def extract(rows, agent_key=""):
    """Every tool call in `rows`, in order, with its result, and the turn's extras."""
    calls, order, results = {}, [], {}
    models = collections.Counter()
    interrupts = 0
    for row in rows:
        ts = parse_ts(row.get("timestamp"))
        msg = row.get("message") or {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        if row.get("type") == "assistant":
            model = msg.get("model") or ""
            if model and not model.startswith("<"):
                models[model] += 1
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in calls:
                    raw = json.dumps(b.get("input"), sort_keys=True, ensure_ascii=False)
                    calls[b["id"]] = {"tool_use_id": b["id"], "name": b.get("name") or "",
                                      "input": raw, "started": ts, "agent_key": agent_key}
                    order.append(b["id"])
        elif row.get("type") == "user":
            if "[Request interrupted" in text_of(content):
                interrupts += 1
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    extra = row.get("toolUseResult")
                    results[b.get("tool_use_id")] = {
                        "error": bool(b.get("is_error")),
                        "text": text_of(b.get("content")),
                        "ended": ts,
                        "agent_id": extra.get("agentId") if isinstance(extra, dict) else None,
                        "async": isinstance(extra, dict) and extra.get("status") == "async_launched",
                    }
    out = []
    for cid in order:
        call = calls[cid]
        res = results.get(cid)
        server, tool = split_tool(call["name"])
        step = dict(call, server=server, tool=tool, ended=res and res["ended"],
                    child_agent=res and res["agent_id"], child_async=bool(res and res["async"]))
        if res is None:
            step.update(outcome="no_result", error_class="", family="", error_text="")
        elif res["error"]:
            cls, family = classify(res["text"])
            step.update(outcome="error", error_class=cls, family=family, error_text=res["text"][:600])
        else:
            step.update(outcome="ok", error_class="", family="", error_text="")
        if step["started"] and step["ended"]:
            step["duration"] = max((step["ended"] - step["started"]).total_seconds(), 0.0)
        else:
            step["duration"] = 0.0
        out.append(step)
    return out, models, interrupts


def with_subagents(main_rows, subagent_dir):
    """The main calls, then each started agent's calls, nested ones included.

    The models counted are the main agent's: that is the model the turn is
    filed under, whatever its subagents ran on.
    """
    steps, models, interrupts = extract(main_rows)
    seen = set()
    queue = [(s["child_agent"], 1) for s in steps if s["name"] in AGENT_TOOLS and s.get("child_agent")]
    while queue:
        agent_id, depth = queue.pop(0)
        if not agent_id or agent_id in seen or depth > MAX_DEPTH or not subagent_dir:
            continue
        seen.add(agent_id)
        path = os.path.join(subagent_dir, "agent-%s.jsonl" % agent_id)
        if not os.path.exists(path):
            continue
        sub, _models, _ = extract(read_rows(path), agent_key=agent_id)
        steps.extend(sub)
        queue.extend((s["child_agent"], depth + 1) for s in sub
                     if s["name"] in AGENT_TOOLS and s.get("child_agent"))
    mark_repeats(steps)
    return steps, models, interrupts, len(seen)


def mark_repeats(steps):
    """An exact repeat is the same tool with the same whole input, by the same agent."""
    seen = collections.Counter()
    for s in steps:
        key = hashlib.sha1(("%s\0%s\0%s" % (s["agent_key"], s["name"], s["input"])).encode()).hexdigest()
        s["input_hash"] = key
        seen[key] += 1
        s["repeat"] = seen[key] > 1
    return max(seen.values()) if seen else 0


def summarize(steps):
    """The turn's numbers, as stored on it."""
    families = collections.Counter(s["family"] for s in steps if s["outcome"] == "error")
    by_hash = collections.Counter(s["input_hash"] for s in steps)
    return {
        "calls": len(steps),
        "sub_calls": sum(1 for s in steps if s["agent_key"]),
        "avoidable": families["avoidable"],
        "failed": families["failed"],
        "guard": families["guard"],
        "environment": families["environment"],
        "repeats": sum(1 for s in steps if s["repeat"]),
        "max_repeat": max(by_hash.values()) if by_hash else 0,
        "tool_seconds": sum(min(s["duration"], 3600) for s in steps),
    }


# --- the digest a reviewer reads ---------------------------------------------------
SECRET_RES = [
    # A bearer token first, so the header rule below does not stop at the word.
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"(?i)(\w*(?:password|passwd|secret|token|api[_-]?key|authorization)\w*)(\s*[=:]\s*)(\"?)"
                r"(?!Bearer \*\*\*)[^\s\"',;]+"), r"\1\2\3***"),
    (re.compile(r"\b(sk|pk|ghp|gho|xox[abp])-[A-Za-z0-9_\-]{12,}"), "***"),
    (re.compile(r"://([^:/\s]+):([^@/\s]+)@"), r"://\1:***@"),
    # Share links carry their key in the query string.
    (re.compile(r"(?i)([?&](?:sk|key|token|sig|signature|access_token|code|auth)=)[^&\s\"'`)]+"), r"\1***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[private key]"),
    # A long run with digits in it is a key or a hash; neither helps a reviewer. A long
    # snake_case name (a test function) has no digit and stays.
    (re.compile(r"\b(?=[A-Za-z_\-]*\d)(?=[0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{40,}\b"), "[long token]"),
]
HEAD, ERR, PROMPT, REPLY, NEXT = 140, 300, 1500, 1500, 1500
# Past this many steps the middle run of plain successes is left out.
KEEP_ENDS = 40


def redact(text):
    for rx, repl in SECRET_RES:
        text = rx.sub(repl, text)
    return text


def one_line(text, size):
    return redact(" ".join((text or "").split()))[:size]


def digest(meta, steps, reply, next_turn=None):
    """A bounded text of one turn for a reviewer: numbers, one line per step, the reply."""
    nums = summarize(steps)
    t0 = next((s["started"] for s in steps if s["started"]), None)
    lines = [
        "Turn %s · model %s · labelled %s%s" % (
            meta.get("id"), meta.get("model") or "unknown", meta.get("intent") or "unlabelled",
            (" · task: %s" % meta["task"]) if meta.get("task") else ""),
        "Prompt: " + one_line(meta.get("prompt"), PROMPT),
        "Numbers: %(calls)s calls (%(sub_calls)s by subagents) · %(avoidable)s avoidable errors · "
        "%(failed)s failed commands · %(guard)s blocked by a guard · %(environment)s environment · "
        "%(repeats)s exact repeats (most of one call: %(max_repeat)s) · tool time %(tool_min).1f min" % dict(
            nums, tool_min=nums["tool_seconds"] / 60),
        "Work-hours of the turn: %.2f" % (meta.get("hours") or 0),
        "",
        "Steps: n | t+min | agent | tool | seconds | outcome | input | error",
    ]
    keep = set(range(len(steps)))
    if len(steps) > 2 * KEEP_ENDS:
        keep = set(range(KEEP_ENDS)) | set(range(len(steps) - KEEP_ENDS, len(steps)))
        keep |= {i for i, s in enumerate(steps) if s["outcome"] != "ok" or s["repeat"]}
    skipped = 0
    for i, s in enumerate(steps):
        if i not in keep:
            skipped += 1
            continue
        if skipped:
            lines.append("… %s steps that succeeded left out …" % skipped)
            skipped = 0
        at = ((s["started"] - t0).total_seconds() / 60) if (t0 and s["started"]) else 0
        outcome = s["outcome"] if s["outcome"] != "error" else "%s/%s" % (s["family"], s["error_class"])
        lines.append("%s | %.1f | %s | %s | %.1f | %s%s | %s | %s" % (
            i + 1, at, s["agent_key"][:8] or "main", s["name"], s["duration"], outcome,
            " (repeat)" if s["repeat"] else "", one_line(s["input"], HEAD),
            one_line(s["error_text"], ERR) if s["outcome"] == "error" else ""))
    if skipped:
        lines.append("… %s steps that succeeded left out …" % skipped)
    lines += ["", "Final reply: " + one_line(reply, REPLY)]
    if next_turn:
        lines.append("Next person turn: %s · feedback %s — %s" % (
            next_turn.get("intent") or "unlabelled", ", ".join(next_turn.get("feedback") or []) or "none",
            one_line(next_turn.get("prompt"), NEXT)))
    return "\n".join(lines)
