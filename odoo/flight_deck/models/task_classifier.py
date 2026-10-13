"""Label turns by what the person's prompt does. Started from the rules of
`backend/flightdeck/tasks.py`.

A turn is one prompt a person wrote and the agent work it caused. Its intent:

- **task**: a new goal.
- **follow_up**: the same goal: the next step, more scope, a fix to what was
  delivered, a reply to the agent.
- **ask**: a question about the work in progress or its result: to
  understand, not to change yet.
- **side**: a question unrelated to the work; not a task.
- **no_work**: a scripted call (cwd under /tmp) or a prompt that caused no
  billed work. Set by rule only.

A follow-up or a question about the work continues a task: `parent` is by default
the session's latest task, and Luna may name an older one it was offered.
`kind` (task, subtask, no_work) is derived from the intent for the rollup.
Messages a person sends while the agent works (steers) are not turns; the
activity reader stores them on the turn they arrived in.

Labels come from stages, the first that is sure wins:

1. **rule**: deterministic checks on the turn and the one before it.
2. **jev**: a TypeSafe Jev choice, accepted only when its probabilities are
   clearly spread. The same call picks a work tag.
3. **llm**: GPT-6 Luna through `codex exec`, batched, for what Jev was not sure
   of, and for every task, which Luna gives a title.
4. **human**: the review queue. Nothing above overwrites it.

Text leaves the machine only after `redact`.

Kept free of Odoo so the rules can be read, and tested, on their own. A turn
here is a dict with `id` (its prompt id), `session`, `start`, `end`, `prompt`,
`final`, `cwd`, `branch`, `calls` (billed rows), `intent` and `title`.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

INTENTS = ("task", "follow_up", "ask", "side", "no_work")
# The intents that continue a task, and the rollup kind of each intent. A side
# question counts under nothing, as a turn with no work does.
CONTINUES = ("follow_up", "ask")
INTENT_KIND = {"task": "task", "follow_up": "subtask", "ask": "subtask",
               "side": "no_work", "no_work": "no_work"}

# Claude Code's own temp root, where its scratchpad lives: a session that
# moved there is still a person working, not a scripted call under /tmp.
SCRATCH_ROOT = "/tmp/claude-"
ANSWER_MAX_CHARS = 60
ANSWER_WINDOW = timedelta(minutes=30)
# A prompt that only says to go on.
_GO_ON = re.compile(
    r"^\s*(ok(ay)?[,. ]*)?(ti[eế]p( t[uụ]c)?|l[aà]m ti[eế]p|continue|go on|go ahead|carry on|next)"
    r"[\s.!…]*$", re.I)
PREV_TURNS = 3
SESSION_TASKS = 5
CANDIDATE_WINDOW = timedelta(hours=24)
MAX_CANDIDATES = 8
# A turn still running is labelled once it ends, or after this long.
SETTLE = timedelta(hours=2)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
# The backend's calibration, made on two choices: below 0.8, when Jev's top
# answer was "task" the LLM called it a subtask 82 times out of 100. Not yet
# measured again on the five intents.
JEV_ACCEPT_TOP1 = 0.8
JEV_ACCEPT_MARGIN = 0.3
JEV_WORKERS = 8
JEV_TIMEOUT = 60

LLM_MODEL = "gpt-6-luna"
LLM_BATCH = 15
LLM_WORKERS = 3
LLM_TIMEOUT = 300
LLM_REVIEW_BELOW = 0.7

NO_WORK_TAG = "none"

# The question is still named "kind" in Jev calls and stored runs.
KIND_QUESTION = {
    "type": "choice",
    "instructions": "What does the NEW PROMPT do, given the earlier work listed in the state?",
    "criteria": {
        "task": "Asks for a deliverable no earlier turn asked for: a feature, fix, analysis, "
                "document or piece of research that could be its own ticket, even in the "
                "same project or session",
        "follow_up": "Asks the agent to do more toward the goal of an earlier turn: go on, "
                     "the next step, more scope, a fix or change to what was delivered, or "
                     "a reply to the agent's question or proposal. A request only to "
                     "explain, justify, compare or report is not a follow-up",
        "ask": "Asks the agent to explain, justify, compare or report on the work in "
               "progress or its result, and asks for no new work or change yet. In "
               "research or design work too, a question for understanding is this, "
               "not a follow-up",
        "side": "A question with no link to the work in the session, such as the date, the "
                "weather or a general fact",
    },
}

LLM_INSTRUCTIONS = """\
You label turns of a person's work with a coding agent. Each item is one NEW prompt,
the agent's last message for it (this_turn_final), the turns before it in the same
session (previous_turns), the tasks of the session so far (session_tasks) and recent
turns from other sessions (other_recent). Label each item:

- intent "task": a deliverable no earlier turn asked for (a feature, fix, analysis,
  document, piece of research) that could be its own ticket. Being in the same
  project or session as earlier work does not make it a follow-up.
- intent "follow_up": asks the agent to do more toward the goal of an earlier turn:
  go on, the next step, more scope, a fix or change to what was delivered, or a reply
  to the agent's question or proposal. A request only to explain, justify, compare
  or report is not a follow-up.
- intent "ask": asks the agent to explain, justify, compare or report on the work in
  progress or its result, and asks for no new work or change yet. In research or
  design work too, a question for understanding ("why…", "explain…", "which fits
  best…") is ask, not a follow-up.
- intent "side": a question with no link to the work in the session, such as the
  date, the weather or a general fact.
- parent_id: for follow_up and ask, the id of the task it continues, from
  session_tasks, previous_turns or other_recent; else "".
- title: for a task, the goal as Verb + Object + Context in Title Case, English, at
  most 8 words (e.g. "Add Ratio Charts to FlightDeck Trends"); else "".
- work_tag: for a task, the one entry of work_tags that fits the work, or "" when
  none does; else "".
- confidence: 0 to 1, for the intent.

Only use ids that appear in the item. Return one label per item, same turn_id.

Items:
"""

# -- outbound text -------------------------------------------------------------

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


def redact(text):
    """Mask credentials and long opaque tokens before text leaves the machine."""
    text = text or ""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


# A line that is only a tag, such as the `<pasted_content id=...>` wrapper
# around pasted text: it names the container, not what was asked.
_TAG_LINE = re.compile(r"^</?[A-Za-z_][\w-]*(\s[^>]*)?>$")


def title_of(text):
    """First line of a prompt that says something, whitespace collapsed, at
    most 80 chars."""
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line and not _TAG_LINE.match(line):
            return line[:80]
    return ""


def ticket_keys(texts, prefixes):
    """Ticket keys such as CRM-12140 in `texts`, for the given project prefixes."""
    if not prefixes:
        return []
    pattern = re.compile(r"\b(%s)-(\d{1,7})\b" % "|".join(re.escape(p) for p in prefixes))
    found = []
    for text in texts:
        for prefix, number in pattern.findall(text or ""):
            key = "%s-%s" % (prefix, number)
            if key not in found:
                found.append(key)
    return found


def path_tag(cwd, patterns):
    """The work tag whose path pattern the working directory contains, or None.

    `patterns` maps a tag name to its comma-separated patterns."""
    for name, pattern in patterns.items():
        if any(p.strip() and p.strip() in (cwd or "") for p in (pattern or "").split(",")):
            return name
    return None


# -- context -------------------------------------------------------------------

def build_contexts(turns):
    """Context per turn id; `turns` sorted by start."""
    by_session, ctx = {}, {}
    for i, t in enumerate(turns):
        prev = by_session.setdefault(t["session"], [])
        last = prev[-1] if prev else None
        gap = None
        if last is not None:
            ref = last["end"] or last["start"]
            gap = max((t["start"] - ref).total_seconds() / 60.0, 0.0)
        others = []
        for o in reversed(turns[:i]):
            if t["start"] - o["start"] > CANDIDATE_WINDOW:
                break
            if o["session"] != t["session"]:
                others.append(o)
                if len(others) >= MAX_CANDIDATES:
                    break
        ctx[t["id"]] = {
            "prev": prev[-PREV_TURNS:][::-1],
            # Earlier tasks of the session, as labelled when the contexts were
            # built; the pass adds the tasks it labels itself.
            "tasks": [p for p in prev if p.get("intent") == "task"][-SESSION_TASKS:][::-1],
            "others": others,
            "gap_min": gap,
            "moved": last is not None and (last["cwd"] != t["cwd"] or last["branch"] != t["branch"]),
        }
        prev.append(t)
    return ctx


def _asked(final):
    """Whether the agent's last message ended on a question."""
    lines = [line.strip() for line in (final or "").splitlines() if line.strip()]
    return bool(lines) and lines[-1].rstrip("*_ )").endswith(("?", "？"))


def rule_label(t, c):
    cwd = t["cwd"] or ""
    if cwd.startswith("/tmp") and not cwd.startswith(SCRATCH_ROOT):
        return {"intent": "no_work", "confidence": 1.0, "reason": "automated"}
    # A turn still running has no billed call yet: it is labelled when the
    # prompt arrives, and checked for this again once it ends. One that never
    # ended counts as over once it has settled.
    over = t["end"] or (t["start"] and t["start"] < datetime.now(timezone.utc) - SETTLE)
    if not t["calls"] and over:
        return {"intent": "no_work", "confidence": 1.0, "reason": "empty"}
    prompt = (t["prompt"] or "").strip()
    if not c["prev"]:
        return None
    if _GO_ON.match(prompt):
        return {"intent": "follow_up", "confidence": 0.95, "reason": "go on"}
    body = " ".join(prompt.split())
    if (_asked(c["prev"][0]["final"]) and len(body) <= ANSWER_MAX_CHARS and "\n" not in prompt
            and c["gap_min"] is not None
            and c["gap_min"] <= ANSWER_WINDOW.total_seconds() / 60):
        return {"intent": "follow_up", "confidence": 0.85, "reason": "reply to a question"}
    return None


def state_text(t, c):
    """What Jev reads: the new prompt and the turns around it."""
    lines = []
    if c["prev"]:
        lines.append("Minutes since the previous turn in this session ended: %.0f" % (c["gap_min"] or 0))
        if c["moved"]:
            lines.append("The working directory or git branch changed since the previous turn.")
        for n, p in enumerate(c["prev"], 1):
            lines.append("Previous turn %s prompt: %s" % (n, redact(p["prompt"])[:600]))
            if p["final"]:
                lines.append("Previous turn %s agent's final message: %s" % (n, redact(p["final"])[:400]))
    else:
        lines.append("This is the first turn of its session.")
    if c["others"]:
        lines.append("Recent turns in other sessions (last 24 hours):")
        for o in c["others"]:
            lines.append("- %s (cwd %s)" % (redact(title_of(o["prompt"])), o["cwd"]))
    lines.append("NEW PROMPT (cwd %s): %s" % (t["cwd"], redact(t["prompt"])[:2000]))
    return "\n".join(lines)


def llm_item(t, c):
    """What Luna reads for one turn."""
    return {
        "turn_id": t["id"],
        "prompt": redact(t["prompt"])[:1500],
        "this_turn_final": redact(t["final"])[:300],
        "cwd": t["cwd"],
        "minutes_since_previous_turn": None if c["gap_min"] is None else round(c["gap_min"]),
        "previous_turns": [{"id": p["id"], "prompt": redact(p["prompt"])[:500],
                            "final": redact(p["final"])[:300]} for p in c["prev"]],
        "session_tasks": [{"id": p["id"], "title": redact(p.get("title") or title_of(p["prompt"]))}
                          for p in c["tasks"]],
        "other_recent": [{"id": o["id"], "title": redact(title_of(o["prompt"])), "cwd": o["cwd"]}
                         for o in c["others"]],
    }


def offered(c):
    return ({p["id"] for p in c["prev"]} | {p["id"] for p in c["tasks"]}
            | {o["id"] for o in c["others"]})


# -- Jev -------------------------------------------------------------------------

def jev_key():
    """TYPESAFE_API_KEY, or the same variable read from TYPESAFE_ENV_FILE, so
    the key stays in the one file jev-browser already uses."""
    key = os.environ.get("TYPESAFE_API_KEY")
    path = os.environ.get("TYPESAFE_ENV_FILE")
    if key or not path:
        return key
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            for line in fh:
                name, _, value = line.strip().removeprefix("export ").partition("=")
                if name == "TYPESAFE_API_KEY":
                    return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def work_question(work_tags):
    """The work-tag choice: the tags a person described, and none of them."""
    criteria = {name: desc for name, desc in work_tags.items() if desc}
    if not criteria:
        return None
    criteria[NO_WORK_TAG] = "None of the other kinds of work fits"
    return {
        "type": "choice",
        "instructions": "What kind of work does the NEW PROMPT ask for?",
        "criteria": criteria,
    }


def spread(probs):
    """(top answer, its probability, margin over the second)."""
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, p1 = ranked[0]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    return top, p1, p1 - p2


def clear(answer):
    return (answer is not None and answer["p1"] >= JEV_ACCEPT_TOP1
            and answer["margin"] >= JEV_ACCEPT_MARGIN)


def jev_call(state, key, work_tags=None, model=JEV_MODEL, questions=None, timeout=JEV_TIMEOUT):
    """One Jev call. Returns a run dict: `answers` maps each question to
    `{kind, p1, margin, probs}`; `error` is set when there is no answer."""
    if questions is None:
        questions = {"kind": KIND_QUESTION}
        work = work_question(work_tags or {})
        if work:
            questions["work"] = work
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    req = urllib.request.Request(JEV_URL, data=body, method="POST", headers={
        "Authorization": "Bearer %s" % key, "Content-Type": "application/json"})
    started = time.monotonic()
    run = {"engine": "jev", "sent": state, "answers": {}, "raw": None, "error": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        run["raw"] = resp
    except urllib.error.HTTPError as e:
        run["error"] = "HTTP %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        run["error"] = str(e)[:300]
    run["latency_ms"] = int((time.monotonic() - started) * 1000)
    for name, answer in ((run["raw"] or {}).get("answers") or {}).items():
        probs = (answer or {}).get("probabilities") or {}
        if probs:
            top, p1, margin = spread(probs)
            run["answers"][name] = {"kind": top, "p1": p1, "margin": margin, "probs": probs}
    if not run["error"] and not run["answers"]:
        run["error"] = "no probabilities in the answer"
    return run


def jev_batch(states, key, work_tags=None, questions=None):
    """One Jev call per state, JEV_WORKERS at a time: `{id: state}` -> `{id: run}`."""
    ids = list(states)
    with ThreadPoolExecutor(JEV_WORKERS) as pool:
        runs = list(pool.map(
            lambda i: jev_call(states[i], key, work_tags, questions=questions), ids))
    return dict(zip(ids, runs))


# -- GPT-6 Luna through codex exec ---------------------------------------------

def llm_ready(timeout=30):
    """Whether codex is signed in, as `(ok, message)`. Checked once a pass, so
    a missing login is one line in the log rather than a failed call per batch."""
    codex = os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"
    try:
        done = subprocess.run([codex, "login", "status"], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:200]
    message = (done.stdout or done.stderr or "").strip().splitlines()
    message = message[-1] if message else ""
    return done.returncode == 0 and "not logged in" not in message.lower(), message


def llm_schema(work_tags):
    return {
        "type": "object", "additionalProperties": False, "required": ["labels"],
        "properties": {"labels": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["turn_id", "intent", "parent_id", "title", "work_tag", "confidence"],
            "properties": {
                "turn_id": {"type": "string"},
                "intent": {"type": "string", "enum": [i for i in INTENTS if i != "no_work"]},
                "parent_id": {"type": "string"},
                "title": {"type": "string"},
                "work_tag": {"type": "string", "enum": [""] + sorted(work_tags)},
                "confidence": {"type": "number"},
            }}}},
    }


def llm_prompt(items, work_tags):
    tags = [{"name": n, "description": d} for n, d in sorted(work_tags.items()) if d]
    return (LLM_INSTRUCTIONS + json.dumps(items, ensure_ascii=False, indent=1)
            + "\n\nwork_tags:\n" + json.dumps(tags, ensure_ascii=False, indent=1))


def codex_exec(prompt, schema, model=LLM_MODEL, timeout=LLM_TIMEOUT):
    """One read-only `codex exec` with a JSON schema: `(raw, error, latency_ms)`."""
    codex = os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"
    raw, error = None, None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fd-tasks-") as tmp:
        schema_path = os.path.join(tmp, "schema.json")
        out = os.path.join(tmp, "out.json")
        with open(schema_path, "w", encoding="utf-8") as fh:
            json.dump(schema, fh)
        try:
            done = subprocess.run(
                [codex, "exec", "-m", model, "-c", 'model_reasoning_effort="low"',
                 "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
                 "--output-schema", schema_path, "-o", out, "-"],
                input=prompt, text=True, cwd=tmp, capture_output=True, timeout=timeout)
            if os.path.exists(out):
                with open(out, encoding="utf-8") as fh:
                    raw = json.load(fh)
            else:
                error = ("codex exited %s: %s" % (
                    done.returncode, (done.stderr or done.stdout or "")[-400:])).strip()
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            error = str(e)[:300]
    return raw, error, int((time.monotonic() - started) * 1000)


def llm_call(items, work_tags=None, model=LLM_MODEL, timeout=LLM_TIMEOUT):
    """Label a batch in one `codex exec` call. Returns a run dict whose `labels`
    maps turn_id to label; `error` is set when the call gave nothing."""
    work_tags = {n: d for n, d in (work_tags or {}).items() if d}
    prompt = llm_prompt(items, work_tags)
    run = {"engine": "llm", "sent": prompt, "labels": {}, "raw": None, "error": None}
    run["raw"], run["error"], run["latency_ms"] = codex_exec(
        prompt, llm_schema(work_tags), model=model, timeout=timeout)
    for label in (run["raw"] or {}).get("labels") or []:
        if isinstance(label, dict) and label.get("turn_id"):
            run["labels"][label["turn_id"]] = label
    return run


def luna_batch(items, work_tags=None):
    """Luna's label per item, LLM_BATCH items to a call: `{id: item}` -> `{id: answer}`.

    An answer holds the label (`raw`, None when Luna gave none), the `batch` it went
    in, and that call's `latency_ms` and `error`.
    """
    ids = list(items)
    batches = [ids[i:i + LLM_BATCH] for i in range(0, len(ids), LLM_BATCH)]
    with ThreadPoolExecutor(LLM_WORKERS) as pool:
        calls = list(pool.map(lambda b: llm_call([items[i] for i in b], work_tags), batches))
    answers = {}
    for n, (batch, run) in enumerate(zip(batches, calls)):
        for i in batch:
            raw = run["labels"].get(items[i]["turn_id"])
            answers[i] = {"batch": n, "raw": raw, "error": run["error"] if not raw else None,
                          "latency_ms": run["latency_ms"]}
    return answers


def check_llm(label, c, jev=None, earlier=None):
    """Luna's label as stored. Anything it could not back up goes to review.

    `earlier(id)` says whether an id is an earlier turn of the same session:
    Luna reads fifteen items at once and may name a turn it saw in another
    item's context, which is still a real turn before this one.
    """
    intent = label.get("intent")
    conf = float(label.get("confidence") or 0.0)
    out = {"intent": intent, "confidence": conf,
           "title": (label.get("title") or "").strip()[:120] if intent == "task" else "",
           "work_tag": (label.get("work_tag") or "") if intent == "task" else ""}
    reasons = []
    if conf < LLM_REVIEW_BELOW:
        reasons.append("Luna was not sure")
    if intent in CONTINUES:
        parent = label.get("parent_id") or ""
        if parent in offered(c) or (parent and earlier and earlier(parent)):
            out["parent"] = parent
        elif parent:
            reasons.append("Luna named a turn it was not offered")
    kind_jev = (jev or {}).get("kind")
    if clear(kind_jev) and kind_jev["kind"] != intent:
        reasons.append("Jev and Luna disagree")
    out["review"] = bool(reasons)
    out["reason"] = "; ".join(reasons)
    return out


# -- the pass ---------------------------------------------------------------------

def default_parents(turns, labels):
    """Give each continuing label with no parent the session's latest task.

    `turns` is every turn sorted by start; a turn's intent is its new label
    when it has one. With no task before it, the previous turn of the session
    is used; with no turn before it, the label keeps no parent and the turn
    counts as its own task.
    """
    last_task, last_turn = {}, {}
    for t in turns:
        lab = labels.get(t["id"])
        intent = lab["intent"] if lab else t.get("intent")
        if lab and lab["intent"] in CONTINUES and not lab.get("parent"):
            parent = last_task.get(t["session"]) or last_turn.get(t["session"])
            if parent:
                lab["parent"] = parent
        if intent == "task":
            last_task[t["session"]] = t["id"]
        last_turn[t["session"]] = t["id"]


def classify(todo, contexts, work_tags, key=None, llm=True, known=None, log=print,
             turns=None, titles_only=(), ask_jev=None, ask_luna=None):
    """Labels for the `todo` turns, and every engine call made for them.

    `known` maps a turn id to the Jev answers an earlier pass already got for
    it; those turns go straight to Luna instead of being asked again.
    `titles_only` holds ids of tasks that are labelled but have no title yet:
    they go to Luna, and only its title and work tag are taken. `turns` is
    every turn, sorted by start, for the default parents.

    Returns `(labels, runs, jev_answers)`: labels maps a turn id to `{intent,
    source, confidence, parent?, title?, work_tag?, review?, reason?, jev?,
    llm?}` (a `titles_only` turn gets `{title, work_tag, titles_only}`); runs is
    a list of run dicts, each with `turn` set; jev_answers holds every Jev
    answer this pass had, used or not.

    `ask_jev(states)` and `ask_luna(items)` make the engine calls, keyed by turn
    id as `jev_batch` and `luna_batch` are, which they default to. A caller
    passes its own to watch or record the calls.
    """
    ask_jev = ask_jev or (lambda states: jev_batch(states, key, work_tags))
    ask_luna = ask_luna or (lambda items: luna_batch(items, work_tags))
    known = known or {}
    titles_only = set(titles_only)
    by_id = {t["id"]: t for t in (turns or todo)}
    labels, runs, jev_todo, llm_todo = {}, [], [], []

    def accept(t, answers):
        """Take Jev's answer for `t`, just asked or kept from an earlier pass.

        A clear answer is the label, and Luna still names a task (and a
        follow-up with nothing to follow in its own session); an unsure one
        goes to Luna. Returns whether the answer was taken.
        """
        kind = answers["kind"]
        if not (clear(kind) and kind["kind"] in INTENTS):
            llm_todo.append(t)
            return False
        lab = {"intent": kind["kind"], "confidence": kind["p1"], "source": "jev", "jev": answers}
        work = answers.get("work")
        if kind["kind"] == "task" and clear(work) and work["kind"] != NO_WORK_TAG:
            lab["work_tag"] = work["kind"]
        labels[t["id"]] = lab
        if kind["kind"] == "task" or (kind["kind"] in CONTINUES and not contexts[t["id"]]["prev"]):
            llm_todo.append(t)
        return True

    for t in todo:
        if t["id"] in titles_only:
            llm_todo.append(t)
            continue
        lab = rule_label(t, contexts[t["id"]])
        if lab:
            labels[t["id"]] = dict(lab, source="rule")
        elif (known.get(t["id"]) or {}).get("kind"):
            accept(t, known[t["id"]])
        else:
            jev_todo.append(t)
    jev_answers = {tid: answers for tid, answers in known.items()}
    if key and jev_todo:
        got = ask_jev({t["id"]: state_text(t, contexts[t["id"]]) for t in jev_todo})
        for t in jev_todo:
            run = got[t["id"]]
            run["turn"] = t["id"]
            runs.append(run)
            answers = run["answers"]
            if not answers.get("kind"):
                continue
            jev_answers[t["id"]] = answers
            run["accepted"] = accept(t, answers)
    elif jev_todo:
        log("tasks: no TypeSafe key; Jev skipped")
        llm_todo += jev_todo
    if llm and llm_todo:
        log("tasks: %s turns to %s in %s batches" % (
            len(llm_todo), LLM_MODEL, -(-len(llm_todo) // LLM_BATCH)))
        items = {t["id"]: llm_item(t, contexts[t["id"]]) for t in llm_todo}
        got = ask_luna(items)
        for t in llm_todo:
            answer = got.get(t["id"]) or {}
            raw = answer.get("raw")
            # One row per turn, holding that turn's item and answer: the
            # batch prompt around it is the same for all fifteen.
            one = {"engine": "llm", "turn": t["id"], "batch": answer.get("batch"),
                   "sent": json.dumps(items[t["id"]], ensure_ascii=False),
                   "raw": raw, "error": answer.get("error"),
                   "latency_ms": answer.get("latency_ms"), "accepted": False,
                   "call_id": answer.get("call_id")}
            runs.append(one)
            if not raw or raw.get("intent") not in INTENTS:
                continue
            if t["id"] in titles_only:
                one["accepted"] = bool(raw.get("title"))
                labels[t["id"]] = {"titles_only": True, "title": (raw.get("title") or "").strip()[:120],
                                   "work_tag": raw.get("work_tag") or ""}
                continue
            jev = jev_answers.get(t["id"]) or {}

            def earlier(pid, t=t):
                other = by_id.get(pid)
                return bool(other) and other["session"] == t["session"] and other["start"] < t["start"]

            lab = check_llm(raw, contexts[t["id"]], jev, earlier)
            one["accepted"] = not lab["review"]
            held = labels.get(t["id"])
            if held and held["source"] == "jev" and held["intent"] == lab["intent"]:
                # Jev was sure and Luna agrees: keep Jev's label, add the
                # title, work tag and parent Luna gave.
                held.update({k: lab[k] for k in ("title", "parent") if lab.get(k)})
                held["work_tag"] = held.get("work_tag") or lab["work_tag"]
                held["llm"] = {"kind": lab["intent"], "confidence": lab["confidence"]}
                continue
            lab.update(source="llm", jev=jev or None, llm={"kind": lab["intent"],
                                                           "confidence": lab["confidence"]})
            labels[t["id"]] = lab
    default_parents(turns or todo, {k: v for k, v in labels.items() if not v.get("titles_only")})
    return labels, runs, jev_answers


def resolve_tasks(turns):
    """The task each turn counts under, by id; `turns` sorted by start.

    A subtask counts under the task at the top of its chain. A turn with no
    work is not work to continue, so a subtask pointing at one continues the
    turn before it in the session; with nothing left the subtask is its own
    task, as is every turn not labelled yet.
    """
    by_id = {t["id"]: t for t in turns}
    earlier, last = {}, {}
    for t in turns:
        earlier[t["id"]] = last.get(t["session"])
        last[t["session"]] = t["id"]

    def root(tid):
        seen = set()
        start = tid
        while tid and tid not in seen:
            seen.add(tid)
            t = by_id.get(tid)
            if t is None:
                break
            if t["kind"] == "no_work" and tid != start:
                tid = earlier.get(tid)
            elif t["kind"] == "subtask" and t.get("parent") in by_id:
                tid = t["parent"]
            else:
                return tid
        return start

    return {t["id"]: root(t["id"]) for t in turns}
