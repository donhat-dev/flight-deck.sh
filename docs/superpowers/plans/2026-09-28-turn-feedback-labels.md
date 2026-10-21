# Turn Feedback Labels, Part A (shadow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every turn and steer message gets Feedback labels (what the prompt says about the agent's last result), stored beside the existing Type label and read by nothing yet, plus a 60-prompt label check the person fills in before anything switches.

**Architecture:** An Odoo-free `feedback_classifier.py` holds the question, the rules, Luna's schema and the stage order, reusing `task_classifier`'s state, redaction and Jev call. A new `turn_feedback.py` holds the label model, the fields on turns and steers, and a queued "Label feedback" code flow (rules → Ask Jev → Ask Luna → Store feedback) that a cron starts every 10 minutes. The Type pipeline in `task.py` is not touched.

**Tech Stack:** Odoo 19 (module `flight_deck`), queue_job, Workflow Studio code flows, TypeSafe Jev `jev-1.13.0`, GPT-6 Luna through `codex exec`, pytest for the Odoo-free code.

**Spec:** `docs/superpowers/specs/2026-09-28-turn-feedback-labels-design.md`. This plan is its step 1 plus the tooling for step 2. Step 3 (the Type change, the migration and the switch of every reader) gets its own plan once the label check passes, because the criteria may change while it is tuned.

## Global Constraints

- Container `flightdeck-odoo19`, database `flightdeck_odoo` on `pgcore-17`, `workers=0`, queue_job channel `root:1`: jobs run one at a time.
- Upgrade: `docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf -d flightdeck_odoo -u flight_deck --stop-after-init --no-http`, then `docker restart flightdeck-odoo19`. Before the restart, `docker exec flightdeck-odoo19 ps -eo pid,etime,args | grep -E "odoo.*(shell|-u )" | grep -v grep` must list nothing; wait while it does.
- Odoo shell: `docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf -d flightdeck_odoo --no-http --log-level=error < script.py`. The container cannot read host `/tmp`; pipe scripts on stdin.
- Text leaves the machine only after `task_classifier.redact`. Never print the TypeSafe key.
- Jev gate: top ≥ 0.8 and margin ≥ 0.3 (`task_classifier.clear`). Jev gives one label; an unclear answer goes to Luna, which may give several.
- A person's Feedback is never overwritten by an engine.
- The 60 label-check turns get no engine Feedback until the person has labelled them.
- Code, comments, labels and log strings in English; plain words; labels are noun phrases.
- New method names must be free on their model (`hasattr(type(env["flightdeck.turn"]), name)` is False before adding).
- No commits: the person commits. Replace every commit step with a `git diff --stat` check.
- Another session edited `task.py`, `task_classifier.py`, `label_run.py` and `task_views.xml` (idle since 15:07). Edit them only where a task says so, with exact-match edits, never whole-file rewrites.

## Review Focus

1. **A person's label survives every later pass**, including a re-run of the backfill: `_store_feedback` skips any row whose `feedback_source` is `human`. Test in Task 4.
2. **An engine outage leaves turns waiting, not labelled empty.** Jev error with Luna off, or Luna error, must keep `feedback_state = todo`; an empty label would read as "moves on". Test in Task 2 (classifier) and Task 4 (store).
3. **Label-check turns are skipped by the pass until the person labels them.** Test in Task 4.
4. **A label the person deactivates stops being offered**, and Luna naming a code outside the active set sends the turn to review rather than storing it. Test in Task 2.
5. **The feedback job and the Type label job never write the same row at once**: both are queue jobs on `root:1`, and the cron only queues. Checked in Task 4 by running the cron twice and seeing one job.

---

### Task 1: One `codex exec` call shared by Type and Feedback

**Files:**
- Modify: `odoo/flight_deck/models/task_classifier.py` (function `llm_call`, around line 433)
- Test: `odoo/tests/test_feedback_classifier.py` (new; `odoo/tests/conftest.py` new)

**Interfaces:**
- Produces: `codex_exec(prompt, schema, model=LLM_MODEL, timeout=LLM_TIMEOUT) -> (raw: dict | None, error: str | None, latency_ms: int)`. `llm_call` keeps its signature and return value.

- [ ] **Step 1: Write the test loader and the failing test**

`odoo/tests/conftest.py`:
```python
"""Load the Odoo-free modules of flight_deck without importing Odoo.

The models package's __init__ imports every Odoo model, so the modules are
loaded into a stand-in package that points at the same folder.
"""
import importlib
import os
import sys
import types

MODELS = os.path.join(os.path.dirname(__file__), "..", "flight_deck", "models")

if "fdm" not in sys.modules:
    pkg = types.ModuleType("fdm")
    pkg.__path__ = [os.path.abspath(MODELS)]
    sys.modules["fdm"] = pkg


def load(name):
    return importlib.import_module("fdm." + name)
```

`odoo/tests/test_feedback_classifier.py`:
```python
import json
import subprocess

from conftest import load

tc = load("task_classifier")


def fake_codex(monkeypatch, answer):
    def run(cmd, input, text, cwd, capture_output, timeout):
        out = cmd[cmd.index("-o") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(answer, fh)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(tc.subprocess, "run", run)


def test_codex_exec_returns_the_output_file(monkeypatch):
    fake_codex(monkeypatch, {"labels": []})
    raw, error, latency = tc.codex_exec("prompt", {"type": "object"})
    assert raw == {"labels": []} and error is None and latency >= 0


def test_llm_call_still_maps_labels_by_turn(monkeypatch):
    fake_codex(monkeypatch, {"labels": [{"turn_id": "p1", "intent": "task"}]})
    run = tc.llm_call([{"turn_id": "p1"}], {})
    assert run["labels"] == {"p1": {"turn_id": "p1", "intent": "task"}}
    assert run["error"] is None and run["engine"] == "llm"
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd /home/nathando/Documents/Projects/flight-deck.sh/odoo/tests && python3 -m pytest -q test_feedback_classifier.py`
Expected: `test_codex_exec_returns_the_output_file` fails with `AttributeError: ... has no attribute 'codex_exec'`; the second passes.

- [ ] **Step 3: Extract `codex_exec` from `llm_call`**

Replace the body of `llm_call` from `codex = ...` to the end with:
```python
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
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m pytest -q test_feedback_classifier.py`
Expected: `2 passed`.

- [ ] **Step 5: Check the diff** — `git -C /home/nathando/Documents/Projects/flight-deck.sh diff --stat -- odoo/flight_deck/models/task_classifier.py odoo/tests`. No commit.

---

### Task 2: `feedback_classifier.py`, the Odoo-free stages

**Files:**
- Create: `odoo/flight_deck/models/feedback_classifier.py`
- Test: `odoo/tests/test_feedback_classifier.py`

**Interfaces:**
- Consumes: `task_classifier.redact`, `rule_label`, `clear`, `jev_batch(states, key, questions=)`, `codex_exec`, `LLM_BATCH`, `LLM_WORKERS`, `LLM_REVIEW_BELOW`.
- Produces:
  - `NONE = "none"`
  - `question(kinds: dict[str, str]) -> dict` (a Jev choice question; `kinds` maps code to criterion)
  - `rule_feedback(t: dict, c: dict) -> dict | None` (`{"codes": [...], "reason": str}`)
  - `turn_item(t, c) -> dict`, `steer_item(item_id, turn_prompt, reply_before, text) -> dict` (Luna items)
  - `steer_state(turn_prompt, reply_before, text) -> str` (Jev state)
  - `luna_batch(items: dict[str, dict], kinds) -> dict[str, dict]` (`{id: {"batch", "raw", "error", "latency_ms"}}`)
  - `classify(items, kinds, key=None, llm=True, ask_jev=None, ask_luna=None, log=print) -> (labels, runs)`; `items` maps an id to `{"state": str, "luna": dict, "rule": dict | None}`; `labels` maps an id to `{"codes": [...], "source": "rule"|"jev"|"llm", "confidence": float, "review": bool, "reason": str, "jev": dict | None}`; `runs` are run dicts with `turn` = the item id and `question = "feedback"`.

- [ ] **Step 1: Write the failing tests** (append to `odoo/tests/test_feedback_classifier.py`)

```python
from datetime import datetime, timedelta, timezone

fb = load("feedback_classifier")
KINDS = {"defect": "It says the result does not work.", "scope": "It adds a requirement.",
         "decision": "It answers the agent's question.", "teach": "It asks to remember a rule."}
T0 = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def turn(prompt, final_before="Done: the page is built.", minutes=5):
    t = {"id": "p2", "rec": 2, "session": 1, "start": T0 + timedelta(minutes=minutes),
         "end": T0 + timedelta(minutes=minutes + 3), "prompt": prompt, "final": "",
         "cwd": "/home/x/proj", "branch": "", "calls": 3, "intent": None}
    prev = {"id": "p1", "rec": 1, "session": 1, "start": T0, "end": T0 + timedelta(minutes=2),
            "prompt": "Build the page", "final": final_before, "cwd": "/home/x/proj",
            "branch": "", "calls": 3, "intent": "task"}
    c = {"prev": [prev], "tasks": [prev], "others": [], "gap_min": 3.0, "moved": False}
    return t, c


def jev_answer(probs):
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    return {"answers": {"feedback": {"kind": ranked[0][0], "p1": ranked[0][1],
                                     "margin": ranked[0][1] - ranked[1][1], "probs": probs}},
            "error": None, "sent": "state", "engine": "jev", "latency_ms": 400}


def items_for(prompt):
    t, c = turn(prompt)
    return {"#2": {"state": "state", "luna": fb.turn_item(t, c), "rule": fb.rule_feedback(t, c)}}


def test_question_offers_every_kind_and_none():
    q = fb.question(KINDS)
    assert q["type"] == "choice" and set(q["criteria"]) == set(KINDS) | {fb.NONE}


def test_first_turn_of_a_session_has_no_feedback():
    t, c = turn("Build it")
    c["prev"] = []
    assert fb.rule_feedback(t, c) == {"codes": [], "reason": "first turn"}


def test_short_reply_to_a_question_is_a_decision():
    t, c = turn("yes", final_before="Done. Shall I also add the export?")
    assert fb.rule_feedback(t, c)["codes"] == ["decision"]


def test_go_on_says_nothing_about_the_result():
    t, c = turn("tiếp tục")
    assert fb.rule_feedback(t, c)["codes"] == []


def test_a_clear_jev_answer_is_the_label():
    items = items_for("The page still shows the old total, it does not work")
    labels, runs = fb.classify(items, KINDS, key="k", llm=False,
                               ask_jev=lambda s: {"#2": jev_answer({"defect": .9, "scope": .05, "none": .05})})
    assert labels["#2"]["codes"] == ["defect"] and labels["#2"]["source"] == "jev"
    assert runs[0]["question"] == "feedback" and runs[0]["accepted"] is True


def test_jev_none_is_no_label():
    items = items_for("Now build the export screen for the same report")
    labels, _ = fb.classify(items, KINDS, key="k", llm=False,
                            ask_jev=lambda s: {"#2": jev_answer({"none": .9, "scope": .05, "defect": .05})})
    assert labels["#2"]["codes"] == []


def test_an_unclear_answer_goes_to_luna_which_may_give_two():
    items = items_for("Remove the prefix rule from the skill and add a rule on external calls")
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["scope", "teach"], "confidence": .8},
                   "error": None, "latency_ms": 9000}}
    labels, runs = fb.classify(items, KINDS, key="k",
                               ask_jev=lambda s: {"#2": jev_answer({"scope": .46, "teach": .32, "none": .22})},
                               ask_luna=lambda i: luna)
    assert labels["#2"]["codes"] == ["scope", "teach"] and labels["#2"]["source"] == "llm"
    assert labels["#2"]["jev"]["main"] == "scope" and [r["engine"] for r in runs] == ["jev", "llm"]


def test_luna_naming_an_unknown_label_goes_to_review():
    items = items_for("Remove the prefix rule")
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["seam"], "confidence": .9},
                   "error": None, "latency_ms": 1}}
    labels, _ = fb.classify(items, KINDS, key=None, ask_luna=lambda i: luna)
    assert labels["#2"]["review"] and labels["#2"]["codes"] == []


def test_luna_not_sure_goes_to_review():
    items = items_for("Remove the prefix rule")
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["scope"], "confidence": .4},
                   "error": None, "latency_ms": 1}}
    labels, _ = fb.classify(items, KINDS, key=None, ask_luna=lambda i: luna)
    assert labels["#2"]["review"] and labels["#2"]["codes"] == ["scope"]


def test_an_engine_outage_leaves_no_label():
    items = items_for("Remove the prefix rule")
    down = {"#2": {"answers": {}, "error": "HTTP 503", "sent": "state", "engine": "jev", "latency_ms": 1}}
    labels, runs = fb.classify(items, KINDS, key="k", llm=False, ask_jev=lambda s: down)
    assert labels == {} and runs[0]["error"] == "HTTP 503"
    luna_down = {"#2": {"batch": 0, "raw": None, "error": "codex exited 1", "latency_ms": 1}}
    labels, _ = fb.classify(items, KINDS, key=None, ask_luna=lambda i: luna_down)
    assert labels == {}


def test_a_steer_state_is_redacted_and_says_the_agent_is_working():
    state = fb.steer_state("Deploy it", "Running the upgrade", "use token=abcd1234efgh5678 instead")
    assert "still working" in state and "abcd1234efgh5678" not in state
```

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m pytest -q test_feedback_classifier.py`
Expected: errors with `ModuleNotFoundError: No module named 'fdm.feedback_classifier'`.

- [ ] **Step 3: Write `feedback_classifier.py`**

```python
"""Label what a prompt says about the result the agent delivered just before it.

The Feedback labels of a turn (spec 2026-09-28-turn-feedback-labels-design):
a person's prompt, or a steer message sent while the agent works, is read
against the agent's work before it. The labels themselves live in
`flightdeck.feedback.kind`; here a kind is `{code: criterion}`.

Stages, the first that is sure wins: the rules, Jev (one choice among the
labels and none, taken when clear), Luna (every label that applies, main one
first), then a person. Jev gives one label: a choice's answers add up to 1, so
a clear top leaves no room for a second, and a prompt that says two things is
unclear and goes to Luna.

Kept free of Odoo like `task_classifier`, whose state, redaction, rules and
engine calls it reuses.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from . import task_classifier as classifier

NONE = "none"
NONE_TEXT = "It moves on without a word on the result."
INSTRUCTIONS = "What does the NEW PROMPT say about the agent's result just before it?"
PROMPT_CHARS = 1500
BEFORE_PROMPT_CHARS = 500
BEFORE_FINAL_CHARS = 400

LUNA_INSTRUCTIONS = """\
You label what a person's NEW prompt to a coding agent says about the result the agent
delivered just before it. Each item has the new prompt and what came before it, most
recent first (`before`: the person's prompt and the agent's final message). An item with
`while_working` true was typed while the agent was still working on `before[0]`.

Give each item every label below that applies, the main one first, or an empty list
when the prompt moves on without a word on the result:

%s

- confidence: 0 to 1, for the labels as a whole.

Return one entry per item, with the item's id.

Items:
"""


def question(kinds):
    """Jev's choice: the labels with a criterion, and none of them."""
    criteria = {code: text for code, text in kinds.items() if text}
    criteria[NONE] = NONE_TEXT
    return {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}


def rule_feedback(t, c):
    """Feedback the rules decide for a turn, or None when they cannot."""
    if not c["prev"]:
        return {"codes": [], "reason": "first turn"}
    lab = classifier.rule_label(t, c)
    if not lab:
        return None
    if lab["intent"] == "answer":
        return {"codes": ["decision"], "reason": lab["reason"]}
    # A scripted call, a prompt with no work, or "go on": nothing said on the result.
    return {"codes": [], "reason": lab["reason"]}


def _item(item_id, prompt, before, while_working=False):
    return {
        "id": item_id,
        "prompt": classifier.redact(prompt)[:PROMPT_CHARS],
        "while_working": while_working,
        "before": [{"prompt": classifier.redact(p["prompt"])[:BEFORE_PROMPT_CHARS],
                    "final": classifier.redact(p["final"])[:BEFORE_FINAL_CHARS]} for p in before],
    }


def turn_item(t, c):
    """What Luna reads for a turn: its prompt and the session's turns before it."""
    return _item("#%s" % t["rec"], t["prompt"], c["prev"])


def steer_item(item_id, turn_prompt, reply_before, text):
    """What Luna reads for a steer message: it, and the turn it arrived in."""
    return _item(item_id, text, [{"prompt": turn_prompt, "final": reply_before}], while_working=True)


def steer_state(turn_prompt, reply_before, text):
    """What Jev reads for a steer message."""
    return "\n".join([
        "The agent is still working on this prompt: " + classifier.redact(turn_prompt)[:PROMPT_CHARS],
        "The agent's last message before the new prompt: "
        + classifier.redact(reply_before)[:BEFORE_FINAL_CHARS],
        "NEW PROMPT, typed while the agent works: " + classifier.redact(text)[:2000],
    ])


def luna_schema(kinds):
    return {
        "type": "object", "additionalProperties": False, "required": ["labels"],
        "properties": {"labels": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "feedback", "confidence"],
            "properties": {
                "id": {"type": "string"},
                "feedback": {"type": "array", "items": {"type": "string", "enum": sorted(kinds)}},
                "confidence": {"type": "number"},
            }}}},
    }


def luna_prompt(items, kinds):
    listed = "\n".join("- %s: %s" % (code, text) for code, text in kinds.items() if text)
    return LUNA_INSTRUCTIONS % listed + json.dumps(items, ensure_ascii=False, indent=1)


def luna_call(items, kinds):
    """One `codex exec` for a batch: a run dict whose `labels` map an item id to its answer."""
    prompt = luna_prompt(items, kinds)
    run = {"engine": "llm", "sent": prompt, "labels": {}}
    run["raw"], run["error"], run["latency_ms"] = classifier.codex_exec(prompt, luna_schema(kinds))
    for label in (run["raw"] or {}).get("labels") or []:
        if isinstance(label, dict) and label.get("id"):
            run["labels"][label["id"]] = label
    return run


def luna_batch(items, kinds):
    """Luna's answer per item, LLM_BATCH to a call: `{id: item}` -> `{id: answer}`."""
    ids = list(items)
    batches = [ids[i:i + classifier.LLM_BATCH] for i in range(0, len(ids), classifier.LLM_BATCH)]
    with ThreadPoolExecutor(classifier.LLM_WORKERS) as pool:
        calls = list(pool.map(lambda b: luna_call([items[i] for i in b], kinds), batches))
    answers = {}
    for n, (batch, run) in enumerate(zip(batches, calls)):
        for i in batch:
            raw = run["labels"].get(i)
            answers[i] = {"batch": n, "raw": raw, "error": run["error"] if not raw else None,
                          "latency_ms": run["latency_ms"]}
    return answers


def read_jev(run):
    """The Feedback answer of one Jev run, or None."""
    answer = (run.get("answers") or {}).get("feedback")
    if not answer:
        return None
    return {"main": answer["kind"], "p1": answer["p1"], "margin": answer["margin"],
            "probs": answer["probs"], "clear": classifier.clear(answer)}


def check_luna(raw, kinds):
    """Luna's labels as stored. A code it made up or an answer it was unsure of goes to review."""
    said = raw.get("feedback") or []
    codes = list(dict.fromkeys(c for c in said if c in kinds))
    confidence = float(raw.get("confidence") or 0.0)
    reasons = []
    if confidence < classifier.LLM_REVIEW_BELOW:
        reasons.append("Luna was not sure")
    if any(c not in kinds for c in said):
        reasons.append("Luna named a label that does not exist")
    return {"codes": codes, "confidence": confidence, "review": bool(reasons),
            "reason": "; ".join(reasons)}


def classify(items, kinds, key=None, llm=True, ask_jev=None, ask_luna=None, log=print):
    """Feedback for each item, and every engine call made for it.

    An item is `{"state": Jev's text, "luna": Luna's item, "rule": rule answer or None}`.
    An item with no answer from any stage gets no label, so it is asked again
    on the next pass rather than stored as saying nothing.
    """
    q = {"feedback": question(kinds)}
    ask_jev = ask_jev or (lambda states: classifier.jev_batch(states, key, questions=q))
    ask_luna = ask_luna or (lambda luna_items: luna_batch(luna_items, kinds))
    labels, runs, jev_todo, luna_todo = {}, [], [], []
    for i, item in items.items():
        rule = item.get("rule")
        if rule is not None:
            labels[i] = {"codes": rule["codes"], "source": "rule", "confidence": 1.0,
                         "review": False, "reason": rule["reason"], "jev": None}
        else:
            jev_todo.append(i)
    if key and jev_todo:
        got = ask_jev({i: items[i]["state"] for i in jev_todo})
        for i in jev_todo:
            run = dict(got[i], turn=i, question="feedback", accepted=False)
            runs.append(run)
            jev = read_jev(run)
            if jev and jev["clear"] and (jev["main"] in kinds or jev["main"] == NONE):
                run["accepted"] = True
                labels[i] = {"codes": [] if jev["main"] == NONE else [jev["main"]],
                             "source": "jev", "confidence": jev["p1"], "review": False,
                             "reason": "", "jev": jev}
            elif not run.get("error"):
                luna_todo.append((i, jev))
    elif jev_todo:
        log("feedback: no TypeSafe key; Jev skipped")
        luna_todo = [(i, None) for i in jev_todo]
    if llm and luna_todo:
        got = ask_luna({i: items[i]["luna"] for i, _jev in luna_todo})
        for i, jev in luna_todo:
            answer = got.get(i) or {}
            raw = answer.get("raw")
            one = {"engine": "llm", "question": "feedback", "turn": i, "batch": answer.get("batch"),
                   "sent": json.dumps(items[i]["luna"], ensure_ascii=False), "raw": raw,
                   "error": answer.get("error"), "latency_ms": answer.get("latency_ms"),
                   "accepted": False}
            runs.append(one)
            if not raw:
                continue
            lab = check_luna(raw, kinds)
            one["accepted"] = not lab["review"]
            labels[i] = dict(lab, source="llm", jev=jev)
    return labels, runs
```

Note on `elif not run.get("error")`: a Jev call that failed goes to neither Luna nor a label, so the turn waits for the next pass (Review Focus 2). A Jev call that answered but was unclear goes to Luna.

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m pytest -q test_feedback_classifier.py`
Expected: `13 passed`. The outage test's first half expects no label and no Luna call when Jev errors: that follows from the `elif`.

- [ ] **Step 5: Check the diff** — `git diff --stat`. No commit.

---

### Task 3: The label model and the fields

**Files:**
- Create: `odoo/flight_deck/models/turn_feedback.py`
- Create: `odoo/flight_deck/data/feedback_data.xml`
- Modify: `odoo/flight_deck/models/__init__.py` (import `turn_feedback` after `task` and `label_run`)
- Modify: `odoo/flight_deck/models/label_run.py` (fields `question`, `steer_id`; `_store_runs`)
- Modify: `odoo/flight_deck/security/ir.model.access.csv` (two rows)
- Modify: `odoo/flight_deck/__manifest__.py` (version `19.0.1.5.0`; data `data/feedback_data.xml` after `data/task_data.xml`)

**Interfaces:**
- Produces:
  - Model `flightdeck.feedback.kind`: `code`, `name`, `family` (`wrong` / `revise` / `control`), `description`, `fix_layer`, `sequence`, `color`, `active`. Method `_criteria() -> dict[str, str]` (active kinds with a description, by sequence).
  - On `flightdeck.turn`: `previous_turn_id`, `previous_prompt`, `previous_final`, `feedback_ids`, `feedback_main_id`, `feedback_source`, `feedback_confidence`, `feedback_state` (`todo` / `done` / `review`), `feedback_detail`, `feedback_gold`, `feedback_no_fit`, `feedback_run_ids`.
  - On `flightdeck.turn.steer`: `feedback_ids`, `feedback_main_id`, `feedback_source`, `feedback_confidence`, `feedback_state`, `feedback_detail`.
  - On `flightdeck.label.run`: `question` (`type` / `feedback`, default `type`), `steer_id`.

- [ ] **Step 1: Check the names are free**

```python
# check_names.py, piped to odoo shell
T, S, K = type(env["flightdeck.turn"]), type(env["flightdeck.turn.steer"]), "flightdeck.feedback.kind"
for name in ["previous_turn_id", "previous_prompt", "previous_final", "feedback_ids", "feedback_main_id",
             "feedback_source", "feedback_confidence", "feedback_state", "feedback_detail",
             "feedback_gold", "feedback_no_fit", "feedback_run_ids", "_cron_label_feedback",
             "_label_feedback_pass", "_link_previous_turns", "_find_feedback_turns",
             "_find_feedback_steers", "_feedback_items", "_ask_jev_feedback",
             "_ask_luna_feedback", "_store_feedback", "_feedback_report",
             "action_compare_label_check", "_label_check_report"]:
    print(name, hasattr(T, name) or name in env["flightdeck.turn"]._fields)
print("steer", [n for n in ["feedback_ids", "feedback_state"] if n in env["flightdeck.turn.steer"]._fields])
print("kind model exists", K in env)
print("run", [n for n in ["question", "steer_id"] if n in env["flightdeck.label.run"]._fields])
```
Expected: every line `False`, `steer []`, `kind model exists False`, `run []`.

- [ ] **Step 2: Write `turn_feedback.py` (models and fields only; methods come in Task 4)**

```python
"""Feedback labels: what a prompt says about the agent's result just before it.

Spec 2026-09-28-turn-feedback-labels-design, step 1: stored beside the Type
label and read by nothing yet. `feedback_classifier` holds the stages; this
file holds the labels, the fields on turns and steers, and the job that
labels them.
"""
import logging

from odoo import api, fields, models

from .task import SOURCES

_logger = logging.getLogger(__name__)

FAMILIES = [("wrong", "Agent wrong"), ("revise", "Revision"), ("control", "Control")]
FEEDBACK_STATES = [("todo", "Not labelled"), ("done", "Labelled"), ("review", "To review")]
FEEDBACK_FIELDS = {"feedback_ids", "feedback_main_id", "feedback_no_fit"}


class FlightdeckFeedbackKind(models.Model):
    _name = "flightdeck.feedback.kind"
    _description = "Feedback label"
    _order = "sequence, id"

    code = fields.Char(required=True, help="What the engines answer with. Never renamed.")
    name = fields.Char(required=True)
    family = fields.Selection(FAMILIES, "Group", required=True)
    description = fields.Text(
        "Criterion", help="What Jev and Luna read when they pick a label. "
                          "A label without one is never picked.")
    fix_layer = fields.Char("Where to fix", help="Where a repeated case of this label is fixed.")
    sequence = fields.Integer(default=10)
    color = fields.Integer()
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint("unique(code)", "One label per code.")

    @api.model
    def _criteria(self):
        """`{code: criterion}` of the active labels the engines may pick."""
        return {k.code: k.description for k in self.search([]) if k.description}


class FlightdeckTurnFeedback(models.Model):
    _inherit = "flightdeck.turn"

    previous_turn_id = fields.Many2one(
        "flightdeck.turn", "Turn before", index=True, ondelete="set null", readonly=True,
        help="The session's turn before this one: the result this prompt's feedback is about.")
    previous_prompt = fields.Text(related="previous_turn_id.prompt_text", string="Prompt before")
    previous_final = fields.Text(related="previous_turn_id.final_head", string="Reply before")
    feedback_ids = fields.Many2many(
        "flightdeck.feedback.kind", "flightdeck_turn_feedback_rel", "turn_id", "kind_id", "Feedback",
        help="What this prompt says about the agent's result just before it.")
    feedback_main_id = fields.Many2one("flightdeck.feedback.kind", "Main feedback", index=True,
                                       ondelete="set null")
    feedback_source = fields.Selection(SOURCES, "Feedback by", index=True, readonly=True)
    feedback_confidence = fields.Float("Feedback confidence", digits=(4, 2), readonly=True)
    feedback_state = fields.Selection(FEEDBACK_STATES, "Feedback", default="todo", index=True,
                                      readonly=True)
    feedback_detail = fields.Json(readonly=True)
    feedback_gold = fields.Boolean(
        "In the label check", index=True, readonly=True,
        help="One of the prompts a person labels by hand before the engines' labels are trusted.")
    feedback_no_fit = fields.Boolean(
        "No label fits", help="For the label check: none of the labels describes this prompt.")
    feedback_run_ids = fields.One2many(
        "flightdeck.label.run", "turn_id", "Feedback engine calls",
        domain=[("question", "=", "feedback")])


class FlightdeckSteerFeedback(models.Model):
    _inherit = "flightdeck.turn.steer"

    feedback_ids = fields.Many2many(
        "flightdeck.feedback.kind", "flightdeck_steer_feedback_rel", "steer_id", "kind_id", "Feedback",
        help="What this message says about the agent's work before it.")
    feedback_main_id = fields.Many2one("flightdeck.feedback.kind", "Main feedback",
                                       ondelete="set null")
    feedback_source = fields.Selection(SOURCES, "Feedback by", readonly=True)
    feedback_confidence = fields.Float("Feedback confidence", digits=(4, 2), readonly=True)
    feedback_state = fields.Selection(FEEDBACK_STATES, "Feedback", default="todo", index=True,
                                      readonly=True)
    feedback_detail = fields.Json(readonly=True)
```

- [ ] **Step 3: Label runs know their question**

In `label_run.py`, class `FlightdeckLabelRun`, after `human_kind`:
```python
    question = fields.Selection([("type", "Type"), ("feedback", "Feedback")], default="type",
                                required=True, index=True)
    steer_id = fields.Many2one("flightdeck.turn.steer", "Steer message", ondelete="cascade")
```
In `_store_runs`, replace the body's loop with:
```python
        for run in runs:
            question = run.get("question") or "type"
            v = {"engine": run["engine"], "turn_id": rec_of.get(run.get("turn")) or False,
                 "probe": probe, "sent": run.get("sent"), "error": run.get("error") or False,
                 "latency_ms": run.get("latency_ms") or 0, "accepted": bool(run.get("accepted")),
                 "question": question, "steer_id": run.get("steer") or False}
            if question == "feedback":
                if run["engine"] == "jev":
                    answer = (run.get("answers") or {}).get("feedback") or {}
                    v.update(kind=answer.get("kind") or False, top1=answer.get("p1") or 0.0,
                             margin=answer.get("margin") or 0.0,
                             answer=run.get("answers") or run.get("raw") or False)
                else:
                    raw = run.get("raw") or {}
                    said = raw.get("feedback")
                    v.update(kind=(said[0] if said else "none") if raw else False,
                             confidence=float(raw.get("confidence") or 0.0), answer=raw or False)
            elif run["engine"] == "jev":
                kind = (run.get("answers") or {}).get("kind") or {}
                work = (run.get("answers") or {}).get("work") or {}
                # A work-tag call asks no kind question; its spread is the tag's.
                spread = kind or work
                v.update(kind=kind.get("kind") or False, top1=spread.get("p1") or 0.0,
                         margin=spread.get("margin") or 0.0, work_tag=work.get("kind") or False,
                         answer=run.get("answers") or run.get("raw") or False)
            else:
                raw = run.get("raw") or {}
                v.update(kind=raw.get("intent") or raw.get("kind") or False,
                         confidence=float(raw.get("confidence") or 0.0),
                         work_tag=raw.get("work_tag") or False, answer=raw or False)
            vals.append(v)
```
In `FlightdeckLabelProbe._compute_stats`, add `("question", "=", "type")` to both `Run.search` domains, so Feedback calls stay out of the Type figures until Task 6.

- [ ] **Step 4: Seed the labels** — `data/feedback_data.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Queues the feedback pass; the job labels turns and steers without feedback. -->
    <record id="cron_label_feedback" model="ir.cron">
        <field name="name">FlightDeck: label feedback</field>
        <field name="model_id" ref="model_flightdeck_turn"/>
        <field name="state">code</field>
        <field name="code">model._cron_label_feedback()</field>
        <field name="interval_number">10</field>
        <field name="interval_type">minutes</field>
        <field name="active" eval="True"/>
    </record>
    <record id="job_function_label_feedback" model="queue.job.function">
        <field name="model_id" ref="model_flightdeck_turn"/>
        <field name="method">_label_feedback_pass</field>
        <field name="allow_commit" eval="True"/>
    </record>

    <!-- The criteria are what Jev and Luna read; a person tunes them here. -->
    <data noupdate="1">
        <record id="feedback_defect" model="flightdeck.feedback.kind">
            <field name="code">defect</field><field name="name">Defect</field>
            <field name="family">wrong</field><field name="sequence">1</field><field name="color">1</field>
            <field name="description">It says the delivered result does not work or behaves wrongly.</field>
            <field name="fix_layer">Verification: a check before reporting done</field>
        </record>
        <record id="feedback_convention" model="flightdeck.feedback.kind">
            <field name="code">convention</field><field name="name">Convention miss</field>
            <field name="family">wrong</field><field name="sequence">2</field><field name="color">1</field>
            <field name="description">It fixes something a written rule, a convention or an earlier instruction of the person already covered: naming, wording, style, process.</field>
            <field name="fix_layer">Instructions, skill, lint</field>
        </record>
        <record id="feedback_context" model="flightdeck.feedback.kind">
            <field name="code">context</field><field name="name">Missing context</field>
            <field name="family">wrong</field><field name="sequence">3</field><field name="color">1</field>
            <field name="description">It states a fact the agent did not know or got wrong (how the business works, who does what, how a system behaves), and the change follows from that fact.</field>
            <field name="fix_layer">Knowledge: DEFINITIONS.MD, facts docs</field>
        </record>
        <record id="feedback_approach" model="flightdeck.feedback.kind">
            <field name="code">approach</field><field name="name">Approach change</field>
            <field name="family">wrong</field><field name="sequence">4</field><field name="color">1</field>
            <field name="description">It rejects the way the agent solved it and names another way, without giving a new fact: revert, or do it differently.</field>
            <field name="fix_layer">Planning: options before building</field>
        </record>
        <record id="feedback_check" model="flightdeck.feedback.kind">
            <field name="code">check</field><field name="name">Check request</field>
            <field name="family">wrong</field><field name="sequence">5</field><field name="color">1</field>
            <field name="description">It asks the agent to test or verify work it reported as done.</field>
            <field name="fix_layer">Verification</field>
        </record>
        <record id="feedback_edit" model="flightdeck.feedback.kind">
            <field name="code">edit</field><field name="name">Content edit</field>
            <field name="family">revise</field><field name="sequence">6</field><field name="color">3</field>
            <field name="description">It changes the wording, length or structure of a text the agent wrote, whose content was right.</field>
            <field name="fix_layer">Writing rules, when it repeats</field>
        </record>
        <record id="feedback_look" model="flightdeck.feedback.kind">
            <field name="code">look</field><field name="name">Look and feel</field>
            <field name="family">revise</field><field name="sequence">7</field><field name="color">3</field>
            <field name="description">It changes how a screen or picture looks: layout, size, colour, order.</field>
            <field name="fix_layer">Design profile, when it repeats</field>
        </record>
        <record id="feedback_scope" model="flightdeck.feedback.kind">
            <field name="code">scope</field><field name="name">Added scope</field>
            <field name="family">revise</field><field name="sequence">8</field><field name="color">3</field>
            <field name="description">It adds a requirement or a next step to the same work, without saying the delivered part is wrong.</field>
            <field name="fix_layer">Prompt, if it was knowable at the start</field>
        </record>
        <record id="feedback_decision" model="flightdeck.feedback.kind">
            <field name="code">decision</field><field name="name">Decision</field>
            <field name="family">control</field><field name="sequence">9</field><field name="color">4</field>
            <field name="description">It answers a question the agent asked, approves what it proposed, or picks one of its options.</field>
            <field name="fix_layer">Fewer or clearer questions</field>
        </record>
        <record id="feedback_explain" model="flightdeck.feedback.kind">
            <field name="code">explain</field><field name="name">Explanation request</field>
            <field name="family">control</field><field name="sequence">10</field><field name="color">4</field>
            <field name="description">It asks why, how or what about the agent's result, to understand it.</field>
            <field name="fix_layer">How replies explain</field>
        </record>
        <record id="feedback_status" model="flightdeck.feedback.kind">
            <field name="code">status</field><field name="name">Status request</field>
            <field name="family">control</field><field name="sequence">11</field><field name="color">4</field>
            <field name="description">It asks what state the work is in or what has been done.</field>
            <field name="fix_layer">FlightDeck views</field>
        </record>
        <record id="feedback_redirect" model="flightdeck.feedback.kind">
            <field name="code">redirect</field><field name="name">Redirect</field>
            <field name="family">control</field><field name="sequence">12</field><field name="color">4</field>
            <field name="description">It stops, pauses or switches the agent's work, or limits what it may do.</field>
            <field name="fix_layer">Orchestration</field>
        </record>
        <record id="feedback_handback" model="flightdeck.feedback.kind">
            <field name="code">handback</field><field name="name">Person's action</field>
            <field name="family">control</field><field name="sequence">13</field><field name="color">4</field>
            <field name="description">It reports something the person did or saw outside the agent: restarted, deployed, it is live.</field>
            <field name="fix_layer">Harness: what the agent could not do itself</field>
        </record>
        <record id="feedback_teach" model="flightdeck.feedback.kind">
            <field name="code">teach</field><field name="name">Standing rule</field>
            <field name="family">control</field><field name="sequence">14</field><field name="color">4</field>
            <field name="description">It asks the agent to remember a rule, or to add it to memory, a skill or the instructions.</field>
            <field name="fix_layer">Check the rule landed in the right place</field>
        </record>
    </data>
</odoo>
```
The cron and job records refer to `_cron_label_feedback` and `_label_feedback_pass` (Task 4). Loading the cron before those methods exist is fine: the code only runs when the cron fires; but do Task 3 and Task 4 in one upgrade so the first firing finds them.

- [ ] **Step 5: Access rows** — append to `security/ir.model.access.csv`:
```
access_feedback_kind_user,feedback.kind.user,model_flightdeck_feedback_kind,group_user,1,0,0,0
access_feedback_kind_manager,feedback.kind.manager,model_flightdeck_feedback_kind,group_manager,1,1,1,1
```

- [ ] **Step 6: Register the file and the data** — `models/__init__.py`: add `from . import turn_feedback` after the line importing `label_run` (it needs `task` and `label_run` loaded). `__manifest__.py`: version `19.0.1.5.0`; add `"data/feedback_data.xml",` after `"data/task_data.xml",`.

(The upgrade runs at the end of Task 4, with the methods in place.)

---

### Task 4: The "Label feedback" flow

**Files:**
- Modify: `odoo/flight_deck/models/turn_feedback.py` (methods on `FlightdeckTurnFeedback`)
- Test: `odoo/tests/check_feedback_pass.py` (odoo shell script, new)

**Interfaces:**
- Consumes: `feedback_classifier.classify`, `rule_feedback`, `turn_item`, `steer_item`, `steer_state`; `task_classifier.build_contexts`, `state_text`, `jev_key`, `jev_batch`, `llm_ready`; `flightdeck.turn._turn_dicts()`, `_label_settings()`; `flightdeck.label.run._store_runs(runs, rec_of)`; `flightdeck.feedback.kind._criteria()`.
- Produces:
  - `_cron_label_feedback()` queues `_label_feedback_pass()`.
  - `_label_feedback_pass(limit=FEEDBACK_LIMIT)` (flow `flight_deck.label_feedback`, "Label feedback") returns the report dict.
  - `_store_feedback(labels, recs)` writes labels on `flightdeck.turn` / `flightdeck.turn.steer` records, skipping a person's.
  - The person's edit rule in `write` on both models.

- [ ] **Step 1: Write the check script** — `odoo/tests/check_feedback_pass.py`:

```python
"""Checks of the feedback pass against the live database, rolled back at the end.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=error < odoo/tests/check_feedback_pass.py
"""
Turn = env["flightdeck.turn"].with_context(fd_label_engine=True)
Turn._link_previous_turns()
Kind = env["flightdeck.feedback.kind"]
defect, scope = Kind.search([("code", "=", "defect")]), Kind.search([("code", "=", "scope")])
a, b, c = Turn.search([("previous_turn_id", "!=", False)], order="started_at desc", limit=3)

# 1. A person's label survives an engine's.
a.with_context(fd_label_engine=False).write({"feedback_ids": [(6, 0, defect.ids)]})
assert a.feedback_source == "human" and a.feedback_state == "done" and a.feedback_main_id == defect
Turn._store_feedback({"#%d" % a.id: {"codes": ["scope"], "source": "jev", "confidence": .9,
                                      "review": False, "reason": "", "jev": None}}, {"#%d" % a.id: a})
assert a.feedback_ids == defect, "an engine overwrote a person's label"

# 2. An engine label is stored, main first, with its state.
Turn._store_feedback({"#%d" % b.id: {"codes": ["scope", "defect"], "source": "llm", "confidence": .5,
                                      "review": True, "reason": "Luna was not sure", "jev": None}},
                     {"#%d" % b.id: b})
assert b.feedback_main_id == scope and b.feedback_ids == scope | defect
assert b.feedback_state == "review" and b.feedback_source == "llm"

# 3. A label-check turn is not picked before the person labels it.
c.write({"feedback_gold": True, "feedback_state": "todo"})
assert c not in Turn._find_feedback_turns(5000), "the pass picked a label-check turn"

# 4. No answer, no label: the turn stays waiting.
before = c.feedback_state
Turn._store_feedback({}, {"#%d" % c.id: c})
assert c.feedback_state == before == "todo"

# 5. The cron queues one job however often it fires.
Turn._cron_label_feedback(); Turn._cron_label_feedback()
jobs = env["queue.job"].search([("identity_key", "=", "fd-feedback-pass"), ("state", "in", ("pending", "enqueued"))])
assert len(jobs) == 1, jobs

print("CHECKS PASSED")
env.cr.rollback()
```

- [ ] **Step 2: Write the methods** — in `turn_feedback.py`, add imports and constants at the top:

```python
from odoo.addons.workflow_studio.workflow import workflow

from . import feedback_classifier as feedback
from . import task_classifier as classifier

FEEDBACK_LIMIT = 300
```

and on `FlightdeckTurnFeedback`:

```python
    # --- a person's label --------------------------------------------------------
    def write(self, vals):
        mine = FEEDBACK_FIELDS & set(vals) and not self.env.context.get("fd_label_engine")
        if mine:
            vals = dict(vals, feedback_source="human", feedback_state="done",
                        feedback_confidence=1.0)
        result = super().write(vals)
        if mine:
            _main_from_labels(self)
        return result

    # --- the pass ------------------------------------------------------------------
    @api.model
    def _cron_label_feedback(self):
        """Queue the feedback pass. As a job it runs one at a time with the label jobs."""
        self.with_delay(description="FlightDeck: label feedback",
                        identity_key="fd-feedback-pass")._label_feedback_pass()
        return True

    @api.model
    @workflow.flow("flight_deck.label_feedback", name="Label feedback")
    def _label_feedback_pass(self, limit=FEEDBACK_LIMIT):
        """Queue job: label turns and steer messages that have no feedback yet.

        Its job function allows commits: the engine calls take minutes, and a
        write held through them collides with the follow tick's writes of live
        turns. So the calls end the transaction, and the labels are written and
        committed on a fresh one.
        """
        self._link_previous_turns()
        turns = self._find_feedback_turns(limit)
        steers = self._find_feedback_steers(limit)
        if not turns and not steers:
            return {"summary": "No turn or steer message without feedback"}
        items, recs = self._feedback_items(turns, steers)
        self.env.cr.commit()
        kinds = self.env["flightdeck.feedback.kind"]._criteria()
        llm = self._label_settings()["llm"] and classifier.llm_ready()[0]
        labels, runs = feedback.classify(
            items, kinds, key=classifier.jev_key(), llm=llm, log=_logger.info,
            ask_jev=self._ask_jev_feedback, ask_luna=lambda i: self._ask_luna_feedback(i, kinds))
        self.env.cr.commit()
        self._store_feedback(labels, recs, runs=runs)
        self.env.cr.commit()
        return self._feedback_report(labels, recs)

    @api.model
    @workflow.task(name="Link each turn to the one before")
    def _link_previous_turns(self):
        """Store each turn's previous turn in its session; returns how many changed."""
        self.env.cr.execute("""
            WITH o AS (SELECT id, lag(id) OVER (PARTITION BY session_ref_id
                                                ORDER BY started_at, id) AS prev
                         FROM flightdeck_turn WHERE started_at IS NOT NULL)
            UPDATE flightdeck_turn t SET previous_turn_id = o.prev
              FROM o WHERE t.id = o.id AND t.previous_turn_id IS DISTINCT FROM o.prev
        """)
        changed = self.env.cr.rowcount
        self.invalidate_model(["previous_turn_id"])
        return changed

    @api.model
    @workflow.task(name="Find turns without feedback",
                   summary=lambda turns: "%d turn(s)" % len(turns))
    def _find_feedback_turns(self, limit):
        """Newest first, so live turns are labelled before the backlog. A turn in
        the label check waits for the person."""
        turns = self.search([("feedback_state", "=", "todo"), ("feedback_gold", "=", False),
                             ("started_at", "!=", False)], order="started_at desc", limit=limit)
        workflow.link(turns)
        return turns

    @api.model
    @workflow.task(name="Find steer messages without feedback",
                   summary=lambda steers: "%d steer message(s)" % len(steers))
    def _find_feedback_steers(self, limit):
        return self.env["flightdeck.turn.steer"].search(
            [("feedback_state", "=", "todo")], order="sent_at desc", limit=limit)

    @api.model
    def _feedback_items(self, turns, steers):
        """What each stage reads, keyed "#<turn id>" and "steer-<id>", and the record of each key."""
        items, recs = {}, {}
        if turns:
            dicts = self._turn_dicts()
            contexts = classifier.build_contexts(dicts)
            wanted = set(turns.ids)
            for t in dicts:
                if t["rec"] not in wanted:
                    continue
                c = contexts[t["id"]]
                key = "#%d" % t["rec"]
                items[key] = {"state": classifier.state_text(t, c), "luna": feedback.turn_item(t, c),
                              "rule": feedback.rule_feedback(t, c)}
                recs[key] = self.browse(t["rec"])
        for steer in steers:
            key = "steer-%d" % steer.id
            prompt = steer.turn_id.prompt_text or steer.turn_id.prompt_head or ""
            reply = self._reply_before(steer)
            items[key] = {"state": feedback.steer_state(prompt, reply, steer.text or ""),
                          "luna": feedback.steer_item(key, prompt, reply, steer.text or ""),
                          "rule": None}
            recs[key] = steer
        return items, recs

    @api.model
    def _reply_before(self, steer):
        """The agent's last text in the steer's turn before the steer was sent."""
        self.env.cr.execute("""
            SELECT text FROM flightdeck_message_text
             WHERE session_ref_id = %s AND agent_ref_id IS NULL AND role = 'assistant'
               AND tool_name IS NULL AND coalesce(text, '') <> ''
               AND ts >= %s AND ts <= %s
             ORDER BY ts DESC, seq DESC LIMIT 1
        """, [steer.session_ref_id.id, steer.turn_id.started_at, steer.sent_at])
        row = self.env.cr.fetchone()
        return row[0] if row else ""

    @api.model
    @workflow.task(name="Ask Jev", input_summary=lambda self, states: {
        k: "%d chars" % len(v) for k, v in list(states.items())[:20]},
        summary=lambda runs: {k: (r.get("error") or "%s %.2f" % (
            (r["answers"].get("feedback") or {}).get("kind"),
            (r["answers"].get("feedback") or {}).get("p1") or 0)) for k, r in list(runs.items())[:20]})
    def _ask_jev_feedback(self, states):
        """Ask Jev (TypeSafe System One) what each prompt says about the result before it.

        Input: per turn ("#<id>") or steer ("steer-<id>"), the redacted text Jev
        reads. Output: per key, the main label, its probability and margin; the
        full request and answer are in Engine calls.
        """
        kinds = self.env["flightdeck.feedback.kind"]._criteria()
        return classifier.jev_batch(states, classifier.jev_key(),
                                    questions={"feedback": feedback.question(kinds)})

    @api.model
    @workflow.task(name="Ask Luna", input_summary=lambda self, items, kinds: {
        k: (v["prompt"] or "")[:80] for k, v in list(items.items())[:20]},
        summary=lambda answers: {k: (a.get("error") or ",".join((a.get("raw") or {}).get("feedback") or [])
                                     or "none") for k, a in list(answers.items())[:20]})
    def _ask_luna_feedback(self, items, kinds):
        """Ask Luna (GPT-6 Luna through codex exec) for every label that applies, 15 items to a call."""
        return feedback.luna_batch(items, kinds)

    @api.model
    @workflow.task(name="Store feedback", summary=lambda n: "%d record(s) labelled" % n)
    def _store_feedback(self, labels, recs, runs=None):
        """Write each label; a person's is kept. A record with no label waits for the next pass."""
        if runs:
            rec_of = {k: (r.turn_id.id if r._name == "flightdeck.turn.steer" else r.id)
                      for k, r in recs.items()}
            for run in runs:
                steer = recs.get(run.get("turn"))
                if steer is not None and steer._name == "flightdeck.turn.steer":
                    run["steer"] = steer.id
            self.env["flightdeck.label.run"]._store_runs(runs, rec_of)
        by_code = {k.code: k for k in self.env["flightdeck.feedback.kind"].with_context(
            active_test=False).search([])}
        written = 0
        for key, lab in labels.items():
            rec = recs.get(key)
            if rec is None or rec.feedback_source == "human":
                continue
            kinds = [by_code[c] for c in lab["codes"] if c in by_code]
            rec.with_context(fd_label_engine=True).write({
                "feedback_ids": [(6, 0, [k.id for k in kinds])],
                "feedback_main_id": kinds[0].id if kinds else False,
                "feedback_source": lab["source"],
                "feedback_confidence": lab["confidence"],
                "feedback_state": "review" if lab["review"] else "done",
                "feedback_detail": {"reason": lab["reason"], "jev": lab.get("jev")},
            })
            written += 1
        return written

    @api.model
    def _feedback_report(self, labels, recs):
        by = {}
        for lab in labels.values():
            by[lab["source"]] = by.get(lab["source"], 0) + 1
        report = {"summary": "%d of %d labelled: %s; %d to review, %d waiting" % (
            len(labels), len(recs), ", ".join("%d by %s" % (n, s) for s, n in sorted(by.items())) or "none",
            sum(1 for lab in labels.values() if lab["review"]), len(recs) - len(labels))}
        for key, lab in list(labels.items())[:19]:
            report[key] = "%s by %s" % (",".join(lab["codes"]) or "none", lab["source"])
        return report
```

and at module level (after `FEEDBACK_FIELDS`):

```python
def _main_from_labels(records):
    """Keep a person's main label among their labels: the only one when there is one."""
    for rec in records:
        if rec.feedback_main_id and rec.feedback_main_id in rec.feedback_ids:
            continue
        main = rec.feedback_ids[:1] if len(rec.feedback_ids) == 1 else rec.feedback_ids.browse()
        rec.with_context(fd_label_engine=True).write({"feedback_main_id": main.id or False})
```

On `FlightdeckSteerFeedback`, the same person's-edit rule:

```python
    def write(self, vals):
        mine = FEEDBACK_FIELDS & set(vals) and not self.env.context.get("fd_label_engine")
        if mine:
            vals = dict(vals, feedback_source="human", feedback_state="done",
                        feedback_confidence=1.0)
        result = super().write(vals)
        if mine:
            _main_from_labels(self)
        return result
```
(`feedback_no_fit` is not on steers; the set intersection simply finds none of it.)

- [ ] **Step 3: Upgrade and restart** (Global Constraints). Expected: the upgrade log ends without a traceback; `-u` retried if it reports a lock.

- [ ] **Step 4: Run the checks**

Run: the odoo shell command in the script's docstring.
Expected: `CHECKS PASSED`.

- [ ] **Step 5: Label five turns for real**

```python
# odoo shell
r = env["flightdeck.turn"]._label_feedback_pass(limit=5)
print(r)
for t in env["flightdeck.turn"].search([("feedback_state", "!=", "todo")], order="started_at desc", limit=5):
    print(t.id, t.feedback_state, t.feedback_source, t.feedback_ids.mapped("code"), (t.prompt_head or "")[:60])
env.cr.commit()
```
Expected: 5 turns labelled or waiting with a reason, five steer messages as well (limit applies to each), a "Label feedback" run in Workflow Studio with the steps Link, Find ×2, Ask Jev, Ask Luna, Store feedback.

- [ ] **Step 6: Check the diff** — `git diff --stat`. No commit.

---

### Task 5: Screens, and the label check

**Files:**
- Create: `odoo/flight_deck/views/feedback_views.xml`
- Modify: `odoo/flight_deck/__manifest__.py` (add `"views/feedback_views.xml",` after `"views/task_views.xml",`)
- Modify: `odoo/flight_deck/models/turn_feedback.py` (`action_compare_label_check`, `_label_check_report`)
- Test: `odoo/tests/check_feedback_views.js` (Playwright, new)

**Interfaces:**
- Consumes: Task 3 fields; `_feedback_items`, `_store_feedback` from Task 4 (the comparison stores runs, never labels).
- Produces: actions `action_feedback_kind`, `action_label_check`; list button `action_compare_label_check` returning a sticky notification with the check's figures; `_label_check_report() -> dict`.

- [ ] **Step 1: The comparison** — add to `FlightdeckTurnFeedback`:

```python
    def action_compare_label_check(self):
        """Ask the engines about the label-check turns the person labelled; never write their labels."""
        done = self.search([("feedback_gold", "=", True), ("feedback_source", "=", "human")])
        asked = done.filtered(lambda t: not t.feedback_run_ids)
        if asked:
            items, recs = self._feedback_items(asked, self.env["flightdeck.turn.steer"])
            self.env.cr.commit()
            kinds = self.env["flightdeck.feedback.kind"]._criteria()
            _labels, runs = feedback.classify(
                items, kinds, key=classifier.jev_key(), llm=classifier.llm_ready()[0],
                ask_jev=self._ask_jev_feedback, ask_luna=lambda i: self._ask_luna_feedback(i, kinds))
            self._store_feedback({}, recs, runs=runs)
        r = self._label_check_report()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": "Label check", "sticky": True, "type": "info" if r["labelled"] else "warning",
            "message": r["summary"]}}

    @api.model
    def _label_check_report(self):
        """The spec's pass figures for the label check."""
        gold = self.search([("feedback_gold", "=", True)])
        done = gold.filtered(lambda t: t.feedback_source == "human")
        no_fit = done.filtered("feedback_no_fit")
        pairs = jev_pairs = jev_main = jev_family = pipe_main = 0
        for t in done - no_fit:
            runs = t.feedback_run_ids.sorted("id")
            jev = runs.filtered(lambda r: r.engine == "jev" and not r.error)[-1:]
            luna = runs.filtered(lambda r: r.engine == "llm" and not r.error)[-1:]
            mine = t.feedback_main_id.code or "none"
            family = t.feedback_main_id.family or "none"
            if jev and jev.accepted:
                jev_pairs += 1
                jev_main += jev.kind == mine
                other = self.env["flightdeck.feedback.kind"].search([("code", "=", jev.kind)], limit=1)
                jev_family += (other.family or "none") == family
            said = (jev if jev and jev.accepted else luna)
            if said:
                pairs += 1
                pipe_main += said.kind == mine
        pct = lambda a, b: "%d%%" % round(100 * a / b) if b else "—"
        return {
            "labelled": len(done), "gold": len(gold),
            "summary": ("%d of %d labelled. No label fits: %s (pass ≤ 10%%). Jev clear on %d: main %s "
                        "(pass ≥ 80%%), group %s (pass ≥ 90%%). Jev then Luna on %d: main %s.") % (
                len(done), len(gold), pct(len(no_fit), len(done)), jev_pairs, pct(jev_main, jev_pairs),
                pct(jev_family, jev_pairs), pairs, pct(pipe_main, pairs)),
        }
```

- [ ] **Step 2: The views** — `views/feedback_views.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- The labels and their criteria -->
    <record id="view_feedback_kind_list" model="ir.ui.view">
        <field name="name">flightdeck.feedback.kind.list</field>
        <field name="model">flightdeck.feedback.kind</field>
        <field name="arch" type="xml">
            <list string="Feedback labels">
                <field name="sequence" widget="handle"/>
                <field name="name"/>
                <field name="code"/>
                <field name="family"/>
                <field name="description"/>
                <field name="fix_layer" optional="show"/>
                <field name="active" optional="hide"/>
            </list>
        </field>
    </record>
    <record id="view_feedback_kind_form" model="ir.ui.view">
        <field name="name">flightdeck.feedback.kind.form</field>
        <field name="model">flightdeck.feedback.kind</field>
        <field name="arch" type="xml">
            <form string="Feedback label">
                <sheet>
                    <group>
                        <group><field name="name"/><field name="code"/><field name="family"/></group>
                        <group><field name="fix_layer"/><field name="color" widget="color_picker"/>
                            <field name="active"/></group>
                    </group>
                    <group><field name="description" placeholder="It says ..."/></group>
                </sheet>
            </form>
        </field>
    </record>
    <record id="action_feedback_kind" model="ir.actions.act_window">
        <field name="name">Feedback labels</field>
        <field name="res_model">flightdeck.feedback.kind</field>
        <field name="view_mode">list,form</field>
        <field name="context">{'active_test': False}</field>
    </record>

    <!-- Feedback on the turn screens -->
    <record id="view_turn_list_feedback" model="ir.ui.view">
        <field name="name">flightdeck.turn.list.feedback</field>
        <field name="model">flightdeck.turn</field>
        <field name="inherit_id" ref="view_turn_list"/>
        <field name="arch" type="xml">
            <xpath expr="//field[@name='intent']" position="after">
                <field name="feedback_ids" widget="many2many_tags" options="{'color_field': 'color'}"
                       optional="show"/>
                <field name="feedback_state" optional="hide"/>
                <field name="feedback_source" optional="hide"/>
            </xpath>
        </field>
    </record>
    <record id="view_turn_form_feedback" model="ir.ui.view">
        <field name="name">flightdeck.turn.form.feedback</field>
        <field name="model">flightdeck.turn</field>
        <field name="inherit_id" ref="view_turn_form"/>
        <field name="arch" type="xml">
            <xpath expr="//field[@name='labeled_at']" position="after">
                <field name="feedback_ids" widget="many2many_tags"
                       options="{'color_field': 'color', 'no_create': True}"/>
                <field name="feedback_main_id" options="{'no_create': True}"
                       domain="[('id', 'in', feedback_ids)]" invisible="not feedback_ids"/>
                <field name="feedback_source" invisible="not feedback_source"/>
                <field name="feedback_state"/>
                <field name="previous_turn_id" invisible="not previous_turn_id"/>
            </xpath>
            <xpath expr="//field[@name='steer_ids']/list/field[@name='text']" position="after">
                <field name="feedback_ids" widget="many2many_tags" options="{'color_field': 'color'}"/>
            </xpath>
        </field>
    </record>
    <record id="view_turn_search_feedback" model="ir.ui.view">
        <field name="name">flightdeck.turn.search.feedback</field>
        <field name="model">flightdeck.turn</field>
        <field name="inherit_id" ref="view_turn_search"/>
        <field name="arch" type="xml">
            <xpath expr="//filter[@name='intent_no_work']" position="after">
                <separator/>
                <filter name="fb_wrong" string="Agent wrong" domain="[('feedback_ids.family', '=', 'wrong')]"/>
                <filter name="fb_revise" string="Revision" domain="[('feedback_ids.family', '=', 'revise')]"/>
                <filter name="fb_control" string="Control" domain="[('feedback_ids.family', '=', 'control')]"/>
                <filter name="fb_review" string="Feedback to review" domain="[('feedback_state', '=', 'review')]"/>
                <filter name="fb_todo" string="Feedback not labelled" domain="[('feedback_state', '=', 'todo')]"/>
            </xpath>
            <xpath expr="//filter[@name='g_intent']" position="after">
                <filter name="g_feedback" string="Main feedback" context="{'group_by': 'feedback_main_id'}"/>
            </xpath>
        </field>
    </record>

    <!-- The label check: the person labels these before the engines are trusted -->
    <record id="view_label_check_list" model="ir.ui.view">
        <field name="name">flightdeck.turn.label.check.list</field>
        <field name="model">flightdeck.turn</field>
        <field name="priority">40</field>
        <field name="arch" type="xml">
            <list string="Label check" create="0">
                <header>
                    <button name="action_compare_label_check" type="object" string="Compare with the engines"
                            display="always"/>
                </header>
                <field name="started_at" string="Start"/>
                <field name="prompt_head" string="Prompt"/>
                <field name="intent"/>
                <field name="feedback_ids" widget="many2many_tags" options="{'color_field': 'color'}"/>
                <field name="feedback_no_fit"/>
                <field name="feedback_source"/>
            </list>
        </field>
    </record>
    <record id="view_label_check_form" model="ir.ui.view">
        <field name="name">flightdeck.turn.label.check.form</field>
        <field name="model">flightdeck.turn</field>
        <field name="priority">40</field>
        <field name="arch" type="xml">
            <form string="Label check" create="0">
                <sheet>
                    <group string="Before">
                        <field name="previous_prompt" readonly="1"/>
                        <field name="previous_final" readonly="1"/>
                    </group>
                    <group string="The prompt">
                        <field name="prompt_text" readonly="1" nolabel="1" colspan="2"/>
                    </group>
                    <group>
                        <group string="Type">
                            <field name="intent" widget="radio"/>
                        </group>
                        <group string="Feedback">
                            <field name="feedback_ids" widget="many2many_checkboxes"/>
                            <field name="feedback_main_id" options="{'no_create': True}"
                                   domain="[('id', 'in', feedback_ids)]" invisible="not feedback_ids"/>
                            <field name="feedback_no_fit"/>
                        </group>
                    </group>
                </sheet>
            </form>
        </field>
    </record>
    <record id="action_label_check" model="ir.actions.act_window">
        <field name="name">Label check</field>
        <field name="res_model">flightdeck.turn</field>
        <field name="view_mode">list,form</field>
        <field name="domain">[('feedback_gold', '=', True)]</field>
        <field name="view_ids" eval="[(5, 0, 0),
            (0, 0, {'view_mode': 'list', 'view_id': ref('view_label_check_list')}),
            (0, 0, {'view_mode': 'form', 'view_id': ref('view_label_check_form')})]"/>
        <field name="help" type="html">
            <p class="o_view_nocontent_smiling_face">No label check drawn yet</p>
            <p>Run odoo/migration/draw_label_check.py to pick the 60 prompts.</p>
        </field>
    </record>

    <menuitem id="menu_label_check" name="Label check" parent="menu_tasks_root"
              action="action_label_check" sequence="35"/>
    <menuitem id="menu_feedback_kind" name="Feedback labels" parent="menu_tasks_root"
              action="action_feedback_kind" sequence="41"/>
</odoo>
```

Before writing, confirm the inherited ids and anchors exist: `grep -n 'id="view_turn_search"\|name="intent_no_work"\|name="g_intent"\|name="labeled_at"\|name="steer_ids"' views/task_views.xml`. If the search view's id differs, use the one the grep shows.

- [ ] **Step 3: Upgrade, restart, and check the screens** — `odoo/tests/check_feedback_views.js`:

```js
// NODE_PATH=$(npm root -g) node odoo/tests/check_feedback_views.js <out dir>
const { chromium } = require("playwright");
const out = process.argv[2] || ".";
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto("http://localhost:8029/web/login");
  await page.fill("input[name=login]", "admin");
  await page.fill("input[name=password]", "admin");
  await page.click("button[type=submit]");
  await page.waitForSelector(".o_main_navbar", { state: "attached" });
  const shots = [
    ["feedback-labels", "/odoo/action-flight_deck.action_feedback_kind"],
    ["turns", "/odoo/action-flight_deck.action_turns"],
    ["label-check", "/odoo/action-flight_deck.action_label_check"],
  ];
  for (const [name, url] of shots) {
    await page.goto("http://localhost:8029" + url, { waitUntil: "commit" });
    await page.waitForSelector(".o_list_view, .o_view_nocontent", { timeout: 20000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${out}/${name}.png` });
    console.log(name, "ok");
  }
  await browser.close();
})();
```
Run with out dir `devtools_mcp_trace/flightdeck-feedback-labels-2026-09-28/`. Expected: three `ok` lines; the labels list shows 14 rows in three groups; the turn list shows a Feedback column.

- [ ] **Step 4: Check the diff** — `git diff --stat`. No commit.

---

### Task 6: Feedback on the Jev and LLM analytics pages

**Files:**
- Modify: `odoo/flight_deck/models/label_run.py` (`FlightdeckLabelProbe`: field `question`, `_compute_stats`, `action_run`, `_jev_html`, `_llm_html`)
- Modify: `odoo/flight_deck/views/task_views.xml` (the probe form: the `question` field next to `source`)

**Interfaces:**
- Consumes: `feedback_classifier.question`, `turn_item`, `luna_call`, `check_luna`, `read_jev`; `flightdeck.feedback.kind._criteria()`.

- [ ] **Step 1: Field and stats** — on `FlightdeckLabelProbe`:
```python
    question = fields.Selection([("type", "Type"), ("feedback", "Feedback")], default="type",
                                required=True, help="Which label the figures and the try-it panel are about.")
```
In `_compute_stats`: `@api.depends("engine", "question")`; replace `("question", "=", "type")` (added in Task 3) with `("question", "=", probe.question)` in both searches. For `human_agree` with `question == "feedback"`, compare with the turns' `feedback_main_id.code` (or `"none"`) where `feedback_source == "human"`:
```python
            if probe.question == "feedback":
                human = self.env["flightdeck.turn"].search(
                    [("id", "in", list(mine)), ("feedback_source", "=", "human")])
                probe.human_pairs = len(human)
                probe.human_agree = (sum(mine[h.id] == (h.feedback_main_id.code or "none") for h in human)
                                     / len(human) if human else 0.0)
            else:
                ...existing human block...
```

- [ ] **Step 2: Try it** — in `action_run`, branch on `self.question == "feedback"` before the existing engine branch:
```python
        if self.question == "feedback":
            kinds = self.env["flightdeck.feedback.kind"]._criteria()
            if self.engine == "jev":
                key = classifier.jev_key()
                if not key:
                    raise UserError("No TypeSafe key: set TYPESAFE_API_KEY or TYPESAFE_ENV_FILE "
                                    "for the Odoo process.")
                state = self.custom_state if self.source == "custom" and self.custom_state \
                    else classifier.state_text(turn, context)
                run = classifier.jev_call(state, key, questions={"feedback": feedback.question(kinds)})
                jev = feedback.read_jev(run)
                run.update(question="feedback", accepted=bool(jev and jev["clear"]))
                vals = {"result_kind": jev and jev["main"] or False,
                        "result_html": self._bars(jev["probs"], jev["clear"]) if jev else False,
                        "result_raw": json.dumps(run["raw"], ensure_ascii=False, indent=1) if run["raw"] else False}
            else:
                item = feedback.turn_item(dict(turn, rec=turn.get("rec") or "custom"), context)
                call = feedback.luna_call([item], kinds)
                raw = call["labels"].get(item["id"])
                checked = feedback.check_luna(raw, kinds) if raw else None
                run = {"engine": "llm", "question": "feedback", "sent": call["sent"], "raw": raw,
                       "error": call["error"], "latency_ms": call["latency_ms"],
                       "accepted": bool(checked and not checked["review"])}
                vals = {"result_kind": ",".join(checked["codes"]) if checked else False,
                        "result_html": False,
                        "result_raw": json.dumps(raw, ensure_ascii=False, indent=1) if raw else False}
        elif self.engine == "jev":
            ...existing...
```
(add `from . import feedback_classifier as feedback` at the top of `label_run.py`; the custom turn dict has no `rec`, which the `dict(turn, rec=...)` covers.)

- [ ] **Step 3: The form** — in the probe form view in `task_views.xml`, next to `source`: `<field name="question" widget="radio" options="{'horizontal': true}"/>`. Find it with `grep -n 'name="source"' views/task_views.xml`.

- [ ] **Step 4: Upgrade, restart, check** — open Jev analytics, choose Feedback, pick turn 1748, run. Expected: `defect` on top, clear. LLM analytics, Feedback, turn 2088: a list such as `scope,teach`. Screenshot both into the evidence folder.

- [ ] **Step 5: Check the diff**. No commit.

---

### Task 7: Draw the label check, then backfill

**Files:**
- Create: `odoo/migration/draw_label_check.py`
- Create: `odoo/migration/backfill_feedback.py`

- [ ] **Step 1: Draw** — `draw_label_check.py`:
```python
"""Pick the 60 prompts of the label check, once, before the backfill.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < odoo/migration/draw_label_check.py

The most recent turns that have a turn before them in their session and are
not scripted or empty: the mix a person really writes. Running it again adds
nothing once 60 are drawn. Set DRY_RUN to list without writing.
"""
DRY_RUN = False
SIZE = 60
Turn = env["flightdeck.turn"].with_context(fd_label_engine=True)
Turn._link_previous_turns()
have = Turn.search_count([("feedback_gold", "=", True)])
todo = Turn.search([("feedback_gold", "=", False), ("feedback_source", "!=", "human"),
                    ("previous_turn_id", "!=", False), ("intent", "not in", ("no_work", False))],
                   order="started_at desc", limit=max(SIZE - have, 0))
for t in todo:
    print(t.id, t.intent, (t.prompt_head or "")[:70])
if not DRY_RUN and todo:
    todo.write({"feedback_gold": True, "feedback_state": "todo", "feedback_ids": [(5, 0, 0)],
                "feedback_main_id": False, "feedback_source": False})
    env.cr.commit()
print("label check: %d drawn now, %d in all" % (len(todo), have + len(todo)))
```
Run it right after Task 4's upgrade, before the cron's first pass has reached far: turns the cron already labelled are reset here, and their engine calls stay in Engine calls for the comparison (the check reads the latest run, so the reset loses nothing).

- [ ] **Step 2: Backfill** — `backfill_feedback.py`:
```python
"""Label every turn and steer message that has no feedback, in passes of 300.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < odoo/migration/backfill_feedback.py

The label check is skipped by the pass itself. Each pass commits. Stops when a
pass labels nothing new, which also covers engines being down.
"""
Turn = env["flightdeck.turn"]
while True:
    left = Turn.search_count([("feedback_state", "=", "todo"), ("feedback_gold", "=", False)])
    report = Turn._label_feedback_pass(limit=300)
    print(left, "left |", report["summary"])
    now = Turn.search_count([("feedback_state", "=", "todo"), ("feedback_gold", "=", False)])
    if now >= left or not now:
        break
```
Before running: pause the feedback cron (`env.ref("flight_deck.cron_label_feedback").active = False; env.cr.commit()`), so the queued job and the script do not label the same turns; turn it back on after.

- [ ] **Step 3: Report the figures** — odoo shell:
```python
T = env["flightdeck.turn"]
print("turns", T.search_count([]), "todo", T.search_count([("feedback_state", "=", "todo")]),
      "review", T.search_count([("feedback_state", "=", "review")]))
env.cr.execute("""select coalesce(k.code, 'none'), t.feedback_source, count(*) from flightdeck_turn t
  left join flightdeck_feedback_kind k on k.id = t.feedback_main_id where t.feedback_state <> 'todo'
  group by 1, 2 order by 3 desc""")
for row in env.cr.fetchall(): print(row)
```
Expected: todo = the 60 label-check turns plus turns that started during the run; the rest labelled or to review.

- [ ] **Step 4: Check the diff**. No commit. Record the figures in the spec's "As built" section and a ledger line in the workspace `MEMORY.md`.

---

## After this plan

The person fills the label check (menu Tasks > Label check), then presses "Compare with the engines". The spec's pass figures come back in one notification. Criteria are tuned in Tasks > Feedback labels until it passes. Then Part B gets its own plan: the Type change and migration, the rollup counts, model stats, the turn-analysis triggers, and the review queue's Feedback chips.
