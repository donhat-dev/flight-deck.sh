"""Group turns into tasks, and cost each task.

A turn (see `activity`) is one human prompt and the agent work it caused. Each
turn gets a kind:

- **task**: a new goal.
- **subtask**: a follow-up, correction or next step of an earlier task
  (`parent_id` points at the task it belongs to).
- **merge**: integrates the results of two or more earlier tasks, such as
  "commit everything" or "summarize today's work" (`merged_ids`).
- **automated**: a scripted call, not a person (cwd under /tmp).
- **empty**: a prompt that caused no billed work (a command, or a prompt
  abandoned before the agent replied).

Labels come from four stages, recorded in `label_source`:

1. `rule`: deterministic checks on the turn and its neighbours.
2. `jev`: a TypeSafe Jev `Choice` over {task, subtask, merge}. Accepted only when
   the probability spread is clear; a flat spread is where Jev is wrong.
3. `llm`: GPT-6 Luna through `codex exec`, batched, for flat Jev answers, merges
   and cross-session follow-ups (both need an id picked from the candidates).
4. `human`: the review queue. Never overwritten by the other stages.

Every stage reads the same mechanical context: the previous turns of the session,
turns of other sessions in the last 24 hours, and whether cwd or branch changed.
Text leaves the machine only after `redact`.

    cd python && ../.venv/bin/python -m flightdeck.tasks            # dry run
    cd python && ../.venv/bin/python -m flightdeck.tasks --apply
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from flightdeck import activity, config, db, pricing

KINDS = ("task", "subtask", "merge", "automated", "empty")

ACK_MAX_CHARS = 24
ACK_WINDOW = timedelta(minutes=30)
PREV_TURNS = 3
CANDIDATE_WINDOW = timedelta(hours=24)
MAX_CANDIDATES = 8
# A turn still running is labeled once it ends, or after this long.
SETTLE = timedelta(hours=2)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
# Set on the first full run: below 0.8, when Jev's top answer was "task" the LLM
# called it a subtask 82 times out of 100.
JEV_ACCEPT_TOP1 = 0.8
JEV_ACCEPT_MARGIN = 0.3
JEV_WORKERS = 8

LLM_MODEL = "gpt-6-luna"
LLM_BATCH = 15
LLM_WORKERS = 3
LLM_TIMEOUT = 300
LLM_REVIEW_BELOW = 0.7

_JEV_QUESTION = {
    "type": "choice",
    "instructions": "How does the NEW PROMPT relate to the earlier work listed in the state?",
    "criteria": {
        "task": "Starts a new goal that is not a follow-up of the earlier turns",
        "subtask": "Continues, corrects, answers a question from, or takes the next step "
                   "of an earlier turn's work",
        "merge": "Integrates or wraps up the results of two or more earlier separate tasks, "
                 "such as committing everything, summarizing the day's work, or "
                 "reconciling branches",
    },
}

_LLM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["labels"],
    "properties": {"labels": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["turn_id", "kind", "parent_id", "merged_ids", "title", "confidence"],
        "properties": {
            "turn_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["task", "subtask", "merge"]},
            "parent_id": {"type": "string"},
            "merged_ids": {"type": "array", "items": {"type": "string"}},
            "title": {"type": "string"},
            "confidence": {"type": "number"},
        }}}},
}

_LLM_INSTRUCTIONS = """\
You label turns of a person's work with a coding agent. Each item is one NEW prompt
with the turns before it in the same session (previous_turns) and recent turns from
other sessions (other_recent). Label each item:

- kind "task": a new goal.
- kind "subtask": a follow-up, correction, answer or next step of earlier work.
  parent_id = the id of the earlier turn it continues, taken from previous_turns or
  other_recent.
- kind "merge": integrates the results of two or more earlier separate tasks (commit
  everything, summarize the day, reconcile branches). merged_ids = those turn ids.
- title: a short noun phrase in English naming the goal (task/merge), else "".
- confidence: 0 to 1.

Use "" for parent_id and [] for merged_ids when they do not apply. Only use ids that
appear in the item. Return one label per item, same turn_id.

Items:
"""

# -- outbound text -----------------------------------------------------------

_REDACTIONS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[private key]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [secret]"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|pwd)"
                r"(\s*[:=]\s*)['\"]?[^\s'\"]+"), r"\1\2[secret]"),
    (re.compile(r"://([^/\s:@]+):([^@\s/]+)@"), r"://\1:[secret]@"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_\w{20,}|"
                r"glpat-[\w-]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16})"), "[secret]"),
    (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "[secret]"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[hex]"),
)


def redact(text: str | None) -> str:
    """Mask credentials and long opaque tokens before text leaves the machine."""
    text = text or ""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def title_of(text: str | None) -> str:
    """First non-empty line of a prompt, whitespace collapsed, at most 80 chars."""
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line[:80]
    return ""


def _ts(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return activity._parse_ts(str(value).replace(" ", "T"))


# -- context -------------------------------------------------------------------

def load_turns(conn) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT turn_id, session_id, started_at, ended_at, prompt_head, final_head, cwd, "
        "git_branch, kind, parent_id, merged_ids, title, label_source, label_confidence, "
        "needs_review, labeled_at FROM turns").fetchall()]
    billed = {r["prompt_id"]: int(r["n"]) for r in conn.execute(
        "SELECT prompt_id, COUNT(*) AS n FROM messages WHERE dup_of IS NULL "
        "AND prompt_id IS NOT NULL GROUP BY prompt_id").fetchall()}
    for r in rows:
        r["start"] = _ts(r["started_at"])
        r["end"] = _ts(r["ended_at"])
        r["billed"] = billed.get(r["turn_id"], 0)
    rows = [r for r in rows if r["start"] is not None]
    rows.sort(key=lambda r: (r["start"], r["turn_id"]))
    return rows


def build_contexts(turns: list[dict]) -> dict:
    """Mechanical context per turn id: previous turns of the session, recent turns
    of other sessions, and the gap and cwd/branch change since the previous turn."""
    by_session: dict = {}
    ctx = {}
    for i, t in enumerate(turns):
        prev = by_session.setdefault(t["session_id"], [])
        last = prev[-1] if prev else None
        gap = None
        if last is not None:
            ref = last["end"] or last["start"]
            gap = max((t["start"] - ref).total_seconds() / 60.0, 0.0)
        others = []
        for o in reversed(turns[:i]):
            if t["start"] - o["start"] > CANDIDATE_WINDOW:
                break
            if o["session_id"] != t["session_id"]:
                others.append(o)
                if len(others) >= MAX_CANDIDATES:
                    break
        ctx[t["turn_id"]] = {
            "prev": prev[-PREV_TURNS:][::-1],
            "others": others,
            "gap_min": gap,
            "moved": last is not None and (last["cwd"] != t["cwd"]
                                           or last["git_branch"] != t["git_branch"]),
        }
        prev.append(t)
    return ctx


def rule_label(t: dict, c: dict) -> dict | None:
    if (t["cwd"] or "").startswith("/tmp"):
        return {"kind": "automated", "confidence": 1.0}
    if t["billed"] == 0:
        return {"kind": "empty", "confidence": 1.0}
    body = " ".join((t["prompt_head"] or "").split())
    if c["prev"] and c["gap_min"] is not None and c["gap_min"] <= ACK_WINDOW.total_seconds() / 60 \
            and len(body) <= ACK_MAX_CHARS and "\n" not in (t["prompt_head"] or "").strip():
        return {"kind": "subtask", "parent": c["prev"][0]["turn_id"], "confidence": 0.9}
    return None


def state_text(t: dict, c: dict) -> str:
    lines = []
    if c["prev"]:
        lines.append(f"Minutes since the previous turn in this session ended: {c['gap_min']:.0f}")
        if c["moved"]:
            lines.append("The working directory or git branch changed since the previous turn.")
        for n, p in enumerate(c["prev"], 1):
            lines.append(f"Previous turn {n} prompt: {redact(p['prompt_head'])[:600]}")
            if p["final_head"]:
                lines.append(f"Previous turn {n} agent's final message: "
                             f"{redact(p['final_head'])[:400]}")
    else:
        lines.append("This is the first turn of its session.")
    if c["others"]:
        lines.append("Recent turns in other sessions (last 24 hours):")
        for o in c["others"]:
            lines.append(f"- {redact(title_of(o['prompt_head']))} (cwd {o['cwd']})")
    lines.append(f"NEW PROMPT (cwd {t['cwd']}): {redact(t['prompt_head'])[:2000]}")
    return "\n".join(lines)


# -- Jev -----------------------------------------------------------------------

def jev_key() -> str | None:
    """TYPESAFE_API_KEY, or the same variable read from TYPESAFE_ENV_FILE (so the
    key stays in the one file jev-browser already uses)."""
    key = os.environ.get("TYPESAFE_API_KEY")
    path = os.environ.get("TYPESAFE_ENV_FILE")
    if key or not path:
        return key
    try:
        for line in open(os.path.expanduser(path), encoding="utf-8"):
            name, _, value = line.strip().removeprefix("export ").partition("=")
            if name == "TYPESAFE_API_KEY":
                return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def jev_classify(state: str, key: str) -> dict | None:
    body = json.dumps({"state": state, "model": JEV_MODEL,
                       "questions": {"kind": _JEV_QUESTION}}).encode()
    req = urllib.request.Request(JEV_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    probs = ((resp.get("answers") or {}).get("kind") or {}).get("probabilities") or {}
    if not probs:
        return None
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, p1 = ranked[0]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    return {"kind": top, "p1": p1, "margin": p1 - p2, "probs": probs}


# -- GPT-6 Luna through codex exec --------------------------------------------

def _llm_item(t: dict, c: dict) -> dict:
    return {
        "turn_id": t["turn_id"],
        "prompt": redact(t["prompt_head"])[:1500],
        "cwd": t["cwd"],
        "minutes_since_previous_turn": None if c["gap_min"] is None else round(c["gap_min"]),
        "previous_turns": [{"id": p["turn_id"], "prompt": redact(p["prompt_head"])[:500],
                            "final": redact(p["final_head"])[:300]} for p in c["prev"]],
        "other_recent": [{"id": o["turn_id"], "title": redact(title_of(o["prompt_head"])),
                          "cwd": o["cwd"]} for o in c["others"]],
    }


def llm_classify(items: list[dict]) -> dict:
    """Label a batch in one `codex exec` call; returns turn_id -> label."""
    codex = os.environ.get("TOKEN_AUDIT_CODEX_BIN") or shutil.which("codex") or "codex"
    with tempfile.TemporaryDirectory(prefix="fd-tasks-") as tmp:
        schema = os.path.join(tmp, "schema.json")
        out = os.path.join(tmp, "out.json")
        with open(schema, "w", encoding="utf-8") as fh:
            json.dump(_LLM_SCHEMA, fh)
        prompt = _LLM_INSTRUCTIONS + json.dumps(items, ensure_ascii=False, indent=1)
        try:
            subprocess.run(
                [codex, "exec", "-m", LLM_MODEL, "-c", 'model_reasoning_effort="low"',
                 "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
                 "--output-schema", schema, "-o", out, "-"],
                input=prompt, text=True, cwd=tmp, capture_output=True, timeout=LLM_TIMEOUT)
            with open(out, encoding="utf-8") as fh:
                labels = json.load(fh).get("labels") or []
        except (OSError, subprocess.SubprocessError, ValueError):
            return {}
    return {lab.get("turn_id"): lab for lab in labels if isinstance(lab, dict)}


def _check_llm(label: dict, t: dict, c: dict) -> dict:
    """Keep only ids the item offered; anything else goes to review."""
    offered = {p["turn_id"] for p in c["prev"]} | {o["turn_id"] for o in c["others"]}
    kind = label.get("kind")
    conf = float(label.get("confidence") or 0.0)
    review = conf < LLM_REVIEW_BELOW
    out = {"kind": kind, "confidence": conf, "title": (label.get("title") or "").strip()[:120]}
    if kind == "subtask":
        parent = label.get("parent_id") or ""
        if parent in offered:
            out["parent"] = parent
        elif c["prev"]:
            out["parent"] = c["prev"][0]["turn_id"]
            review = True
        else:
            review = True
    elif kind == "merge":
        merged = [m for m in label.get("merged_ids") or [] if m in offered]
        out["merged"] = merged
        review = review or len(merged) < 2
    out["review"] = review
    return out


# -- the run -------------------------------------------------------------------

def _pending(t: dict, now: datetime) -> bool:
    if t["label_source"] == "human" or t["labeled_at"]:
        return False
    return t["end"] is not None or now - t["start"] > SETTLE


def classify(turns: list[dict], contexts: dict, todo: list[dict], remote: bool,
             log=print) -> dict:
    """Labels for the `todo` turns: turn_id -> {kind, source, confidence, ...}."""
    labels: dict = {}
    jev_todo, llm_todo = [], []
    for t in todo:
        c = contexts[t["turn_id"]]
        lab = rule_label(t, c)
        if lab:
            labels[t["turn_id"]] = {**lab, "source": "rule"}
        else:
            jev_todo.append(t)
    key = jev_key() if remote else None
    if remote and not key:
        log("tasks: no TYPESAFE_API_KEY; skipping Jev")
    if key and jev_todo:
        with ThreadPoolExecutor(JEV_WORKERS) as pool:
            answers = list(pool.map(
                lambda t: jev_classify(state_text(t, contexts[t["turn_id"]]), key), jev_todo))
        for t, a in zip(jev_todo, answers):
            c = contexts[t["turn_id"]]
            clear = a is not None and a["p1"] >= JEV_ACCEPT_TOP1 and a["margin"] >= JEV_ACCEPT_MARGIN
            # Jev cannot pick ids: a merge, or a follow-up with no earlier turn in
            # its session, needs the LLM to name what it continues.
            needs_id = a is not None and (a["kind"] == "merge"
                                          or (a["kind"] == "subtask" and not c["prev"]))
            if clear and not needs_id:
                lab = {"kind": a["kind"], "confidence": a["p1"], "source": "jev",
                       "detail": {"jev": a["probs"]}}
                if a["kind"] == "subtask":
                    lab["parent"] = c["prev"][0]["turn_id"]
                labels[t["turn_id"]] = lab
            else:
                t["_jev"] = a
                llm_todo.append(t)
    elif remote:
        llm_todo = jev_todo
    if remote and llm_todo:
        batches = [llm_todo[i:i + LLM_BATCH] for i in range(0, len(llm_todo), LLM_BATCH)]
        log(f"tasks: {len(llm_todo)} turns to {LLM_MODEL} in {len(batches)} batches")
        with ThreadPoolExecutor(LLM_WORKERS) as pool:
            results = list(pool.map(
                lambda b: llm_classify([_llm_item(t, contexts[t["turn_id"]]) for t in b]),
                batches))
        for batch, got in zip(batches, results):
            for t in batch:
                raw = got.get(t["turn_id"])
                if not raw or raw.get("kind") not in ("task", "subtask", "merge"):
                    continue
                lab = _check_llm(raw, t, contexts[t["turn_id"]])
                jev = t.get("_jev") or {}
                # A clear Jev answer the LLM contradicts is a disagreement to review.
                if jev and jev["p1"] >= JEV_ACCEPT_TOP1 and jev["margin"] >= JEV_ACCEPT_MARGIN \
                        and jev["kind"] != lab["kind"]:
                    lab["review"] = True
                lab["source"] = "llm"
                lab["detail"] = {"jev": jev.get("probs")}
                labels[t["turn_id"]] = lab
    return labels


def resolve(turns: list[dict], labels: dict) -> dict:
    """Point every subtask at the root task of what it continues.

    An empty or automated turn is not work to continue, so the parent moves
    back to the session's previous turn; with none left the subtask has no
    parent and counts as its own task.
    """
    kind = {t["turn_id"]: t["kind"] for t in turns}
    parent = {t["turn_id"]: t["parent_id"] for t in turns}
    earlier: dict = {}
    last_in_session: dict = {}
    for t in turns:
        earlier[t["turn_id"]] = last_in_session.get(t["session_id"])
        last_in_session[t["session_id"]] = t["turn_id"]
    for tid, lab in labels.items():
        kind[tid] = lab["kind"]
        parent[tid] = lab.get("parent")

    def root(tid):
        seen = set()
        while tid and tid not in seen:
            seen.add(tid)
            if kind.get(tid) in ("empty", "automated"):
                tid = earlier.get(tid)
            elif kind.get(tid) == "subtask" and parent.get(tid):
                tid = parent[tid]
            else:
                break
        return tid if tid and kind.get(tid) not in ("empty", "automated") else None

    for tid, lab in labels.items():
        if lab["kind"] == "subtask" and lab.get("parent"):
            lab["parent"] = root(lab["parent"])
    return labels


def write_labels(conn, turns: list[dict], labels: dict) -> None:
    by_id = {t["turn_id"]: t for t in turns}
    now = datetime.now(timezone.utc).isoformat()
    for tid, lab in labels.items():
        t = by_id[tid]
        title = lab.get("title") or title_of(t["prompt_head"])
        conn.execute(
            "UPDATE turns SET kind=?, parent_id=?, merged_ids=?, title=?, label_source=?, "
            "label_confidence=?, label_detail=?, needs_review=?, labeled_at=? "
            "WHERE turn_id=? AND (label_source IS NULL OR label_source <> 'human')",
            (lab["kind"], lab.get("parent"),
             json.dumps(lab["merged"]) if lab.get("merged") else None,
             title, lab["source"], lab.get("confidence"),
             json.dumps(lab.get("detail")) if lab.get("detail") else None,
             int(bool(lab.get("review"))), now, tid))
    conn.commit()


def label_pending(conn, remote: bool = True, apply: bool = True, limit: int | None = None,
                  log=print) -> dict:
    turns = load_turns(conn)
    contexts = build_contexts(turns)
    now = datetime.now(timezone.utc)
    todo = [t for t in turns if _pending(t, now)]
    if limit:
        todo = todo[:limit]
    labels = resolve(turns, classify(turns, contexts, todo, remote, log=log))
    if apply:
        write_labels(conn, turns, labels)
    stats: dict = {"pending": len(todo), "labeled": len(labels),
                   "review": sum(1 for v in labels.values() if v.get("review"))}
    for v in labels.values():
        k = f"{v['source']}:{v['kind']}"
        stats[k] = stats.get(k, 0) + 1
    return stats


def set_label(conn, turn_id: str, kind: str, parent_id: str | None = None,
              merged_ids: list[str] | None = None, title: str | None = None) -> bool:
    """A person's label. It wins over every other stage and leaves the queue.

    A subtask with no parent given continues the session's previous task; a
    merge with no ids given keeps the ids it already has.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if kind == "subtask" and not parent_id:
        turns = load_turns(conn)
        mine = next((t for t in turns if t["turn_id"] == turn_id), None)
        if mine is not None:
            before = [t for t in turns if t["session_id"] == mine["session_id"]
                      and t["start"] < mine["start"]]
            if before:
                probe = {turn_id: {"kind": "subtask", "parent": before[-1]["turn_id"]}}
                parent_id = resolve(turns, probe)[turn_id]["parent"]
    merged = None
    if kind == "merge":
        if merged_ids:
            merged = json.dumps(merged_ids)
        else:
            row = conn.execute("SELECT merged_ids FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            merged = row[0] if row else None
    cur = conn.execute(
        "UPDATE turns SET kind=?, parent_id=?, merged_ids=?, title=COALESCE(?, title), "
        "label_source='human', label_confidence=1.0, needs_review=0, labeled_at=? "
        "WHERE turn_id=?",
        (kind, parent_id if kind == "subtask" else None, merged,
         title, datetime.now(timezone.utc).isoformat(), turn_id))
    conn.commit()
    return cur.rowcount > 0


# -- cost per task ---------------------------------------------------------------

def rollup(conn, since: str | None = None, limit: int = 50) -> dict:
    """Cost, tokens and agent-hours per task, with the review queue.

    A task's figures include its subtasks; a merge keeps its own cost. Only rows
    read from a transcript carry a turn, so sessions whose transcript is gone
    are not in this view.
    """
    turns = load_turns(conn)
    kind = {t["turn_id"]: t["kind"] for t in turns}
    parent = {t["turn_id"]: t["parent_id"] for t in turns}

    def root(tid):
        seen = set()
        while kind.get(tid) == "subtask" and parent.get(tid) in kind and tid not in seen:
            seen.add(tid)
            tid = parent[tid]
        return tid

    own_cost: dict = {}
    own_tokens: dict = {}
    for r in conn.execute(
            "SELECT prompt_id, model, ts, input_tokens, cache_read, cache_create_5m, "
            "cache_create_1h, output_tokens, speed, inference_geo FROM messages "
            "WHERE dup_of IS NULL AND prompt_id IS NOT NULL").fetchall():
        args = (r["model"], int(r["input_tokens"] or 0), int(r["cache_read"] or 0),
                int(r["cache_create_5m"] or 0), int(r["cache_create_1h"] or 0),
                int(r["output_tokens"] or 0))
        cost = pricing.message_cost(*args, r["ts"], r["speed"], r["inference_geo"]) or 0.0
        pid = r["prompt_id"]
        own_cost[pid] = own_cost.get(pid, 0.0) + cost
        own_tokens[pid] = own_tokens.get(pid, 0) + sum(args[1:])
    own_hours = {r["prompt_id"]: float(r["s"] or 0) / 3600.0 for r in conn.execute(
        "SELECT prompt_id, SUM(busy_seconds) AS s FROM agent_activity "
        "WHERE method='transcript' GROUP BY prompt_id").fetchall()}

    tasks: dict = {}
    for t in turns:
        rid = root(t["turn_id"])
        agg = tasks.setdefault(rid, {"task_id": rid, "turns": 0, "cost": 0.0, "tokens": 0,
                                     "agent_hours": 0.0, "last_ts": None})
        agg["turns"] += 1
        agg["cost"] += own_cost.get(t["turn_id"], 0.0)
        agg["tokens"] += own_tokens.get(t["turn_id"], 0)
        agg["agent_hours"] += own_hours.get(t["turn_id"], 0.0)
        end = t["end"] or t["start"]
        agg["last_ts"] = max(agg["last_ts"] or end, end)
    by_id = {t["turn_id"]: t for t in turns}
    rows = []
    for rid, agg in tasks.items():
        t = by_id.get(rid)
        if t is None or (since and t["started_at"][:10] < since[:10]):
            continue
        rows.append({
            **agg, "cost": round(agg["cost"], 4), "agent_hours": round(agg["agent_hours"], 3),
            "cost_own": round(own_cost.get(rid, 0.0), 4),
            "last_ts": agg["last_ts"].isoformat() if agg["last_ts"] else None,
            "kind": t["kind"], "title": t["title"] or title_of(t["prompt_head"]),
            "session_id": t["session_id"], "cwd": t["cwd"], "started_at": t["started_at"],
            "merged_ids": json.loads(t["merged_ids"]) if t["merged_ids"] else [],
            "label_source": t["label_source"], "label_confidence": t["label_confidence"],
            "needs_review": bool(t["needs_review"]),
        })
    # Rows are roots only, so a subtask here is one with no parent: its own task.
    work = [r for r in rows if r["kind"] not in ("automated", "empty")]
    costs = sorted(r["cost"] for r in work)

    def pct(p):
        return round(costs[min(len(costs) - 1, int(p * len(costs)))], 4) if costs else 0.0

    in_range = [t for t in turns if not since or t["started_at"][:10] >= since[:10]]
    details = {r["turn_id"]: r["label_detail"] for r in conn.execute(
        "SELECT turn_id, label_detail FROM turns WHERE needs_review=1").fetchall()}

    def jev_kind(tid):
        try:
            probs = (json.loads(details.get(tid) or "{}") or {}).get("jev") or {}
        except ValueError:
            return None
        return max(probs, key=probs.get) if probs else None

    review = [{"turn_id": t["turn_id"], "started_at": t["started_at"], "kind": t["kind"],
               "parent_id": t["parent_id"], "label_source": t["label_source"],
               "label_confidence": t["label_confidence"], "cwd": t["cwd"],
               "jev_kind": jev_kind(t["turn_id"]),
               "prompt": title_of(t["prompt_head"]), "cost": round(own_cost.get(t["turn_id"], 0.0), 4)}
              for t in in_range if t["needs_review"] and t["label_source"] != "human"]
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return {
        "summary": {
            "tasks": len(work), "turns": len(in_range),
            "unlabeled": sum(1 for t in in_range if not t["kind"]),
            "needs_review": len(review),
            "cost": round(sum(r["cost"] for r in rows), 2),
            "median_cost": pct(0.5), "p90_cost": pct(0.9),
            "by_source": {s: sum(1 for t in in_range if t["label_source"] == s)
                          for s in ("rule", "jev", "llm", "human")},
        },
        "tasks": rows[:limit],
        "review": review[:limit],
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Label turns as tasks, subtasks and merges.")
    ap.add_argument("--apply", action="store_true", help="write labels (default: dry run)")
    ap.add_argument("--no-remote", action="store_true", help="rules only; send nothing out")
    ap.add_argument("--limit", type=int, default=None, help="label at most N pending turns")
    args = ap.parse_args(argv)
    cfg = config.load(os.environ.get("TOKEN_AUDIT_CONFIG", "config.toml"))
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    db.connect(cfg["db_path"]).close()      # schema migration
    print(label_pending(conn, remote=not args.no_remote, apply=args.apply, limit=args.limit))


if __name__ == "__main__":
    main()
