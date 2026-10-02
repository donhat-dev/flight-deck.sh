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
NONE_TEXT = ("It moves on without a word on the result: to a new piece of work, or a question "
             "about something else.")
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
when the prompt moves on without a word on the result. Most prompts carry one label or
none: a label is for what the prompt says about the result just before it, so a prompt
that starts other work, or asks about something else, carries none.


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


# Feedback that only asks the agent to explain or report: its prompt is a question
# about the work, whatever Type an engine gave it.
PULL_ONLY = frozenset({"explain", "status"})


def type_from_feedback(intent, codes):
    """`ask` for a follow-up whose Feedback only asks to explain or report; else None."""
    if intent == "follow_up" and codes and set(codes) <= PULL_ONLY:
        return "ask"
    return None


def rule_feedback(t, c):
    """Feedback the rules decide for a turn, or None when they cannot."""
    if not c["prev"]:
        return {"codes": [], "reason": "first turn"}
    lab = classifier.rule_label(t, c)
    if not lab:
        return None
    if lab["reason"] == "reply to a question":
        return {"codes": ["decision"], "reason": lab["reason"]}
    if lab["reason"] == "empty":
        # No billed call says nothing about what the prompt says (and spend may
        # not be read yet): the engines are asked.
        return None
    # A scripted call, or "go on": nothing said on the result.
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


def agrees(main, codes, said):
    """Whether an engine's main label matches a person's: their main one, else any of theirs, else none."""
    if main:
        return said == main
    if codes:
        return said in codes
    return said == NONE


def agrees_family(main, codes, said, family_of):
    """The same, by group: `family_of` maps a code to its group."""
    if main:
        return family_of.get(said, NONE) == family_of.get(main)
    if codes:
        return family_of.get(said, NONE) in {family_of.get(c) for c in codes}
    return said == NONE


def classify(items, kinds, key=None, llm=True, ask_jev=None, ask_luna=None, log=print):
    """Feedback for each item, and every engine call made for it.

    An item is `{"state": Jev's text, "luna": Luna's item, "rule": rule answer
    or None, "jev": Jev's unclear answer from an earlier pass, or absent}`.
    Returns `(labels, runs, pending)`: labels maps an item id to `{codes,
    source, confidence, review, reason, jev}`, codes main first; runs are run
    dicts with `turn` set to the item id and `question` to "feedback";
    pending maps an item Jev was unclear on and Luna did not answer to Jev's
    answer, kept so the next pass goes straight to Luna.

    An item no stage answered gets no label, so it is asked again on the next
    pass rather than stored as saying nothing. A Jev call that failed is not
    passed to Luna either: the next pass asks Jev again.
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
        elif item.get("jev"):
            luna_todo.append((i, item["jev"]))
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
        luna_todo += [(i, None) for i in jev_todo]
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
    pending = {i: jev for i, jev in luna_todo if jev and i not in labels}
    return labels, runs, pending
