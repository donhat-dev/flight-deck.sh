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


# -- feedback_classifier ----------------------------------------------------------

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
    labels, runs, _pending = fb.classify(items, KINDS, key="k", llm=False,
                               ask_jev=lambda s: {"#2": jev_answer({"defect": .9, "scope": .05, "none": .05})})
    assert labels["#2"]["codes"] == ["defect"] and labels["#2"]["source"] == "jev"
    assert runs[0]["question"] == "feedback" and runs[0]["accepted"] is True


def test_jev_none_is_no_label():
    items = items_for("Now build the export screen for the same report")
    labels, _runs, _pending = fb.classify(items, KINDS, key="k", llm=False,
                            ask_jev=lambda s: {"#2": jev_answer({"none": .9, "scope": .05, "defect": .05})})
    assert labels["#2"]["codes"] == []


def test_an_unclear_answer_goes_to_luna_which_may_give_two():
    items = items_for("Remove the prefix rule from the skill and add a rule on external calls")
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["scope", "teach"], "confidence": .8},
                   "error": None, "latency_ms": 9000}}
    labels, runs, _pending = fb.classify(items, KINDS, key="k",
                               ask_jev=lambda s: {"#2": jev_answer({"scope": .46, "teach": .32, "none": .22})},
                               ask_luna=lambda i: luna)
    assert labels["#2"]["codes"] == ["scope", "teach"] and labels["#2"]["source"] == "llm"
    assert labels["#2"]["jev"]["main"] == "scope" and [r["engine"] for r in runs] == ["jev", "llm"]


def test_luna_naming_an_unknown_label_goes_to_review():
    items = items_for("Remove the prefix rule")
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["seam"], "confidence": .9},
                   "error": None, "latency_ms": 1}}
    labels, _runs, _pending = fb.classify(items, KINDS, key=None, ask_luna=lambda i: luna)
    assert labels["#2"]["review"] and labels["#2"]["codes"] == []


def test_luna_not_sure_goes_to_review():
    items = items_for("Remove the prefix rule")
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["scope"], "confidence": .4},
                   "error": None, "latency_ms": 1}}
    labels, _runs, _pending = fb.classify(items, KINDS, key=None, ask_luna=lambda i: luna)
    assert labels["#2"]["review"] and labels["#2"]["codes"] == ["scope"]


def test_an_engine_outage_leaves_no_label():
    items = items_for("Remove the prefix rule")
    down = {"#2": {"answers": {}, "error": "HTTP 503", "sent": "state", "engine": "jev", "latency_ms": 1}}
    labels, runs, _pending = fb.classify(items, KINDS, key="k", llm=False, ask_jev=lambda s: down)
    assert labels == {} and runs[0]["error"] == "HTTP 503"
    luna_down = {"#2": {"batch": 0, "raw": None, "error": "codex exited 1", "latency_ms": 1}}
    labels, _runs, _pending = fb.classify(items, KINDS, key=None, ask_luna=lambda i: luna_down)
    assert labels == {}


def test_a_steer_state_is_redacted_and_says_the_agent_is_working():
    state = fb.steer_state("Deploy it", "Running the upgrade", "use token=abcd1234efgh5678 instead")
    assert "still working" in state and "abcd1234efgh5678" not in state


def test_a_prompt_with_no_billed_call_is_still_asked():
    t, c = turn("1. Duyệt 2. Đồng ý 3. Đã đăng nhập")
    t["calls"] = 0
    assert fb.rule_feedback(t, c) is None


def test_jev_error_with_luna_on_sends_nothing_to_luna():
    items = items_for("Remove the prefix rule")
    down = {"#2": {"answers": {}, "error": "HTTP 503", "sent": "state", "engine": "jev", "latency_ms": 1}}
    asked = []
    labels, _runs, pending = fb.classify(items, KINDS, key="k", ask_jev=lambda s: down,
                                         ask_luna=lambda i: asked.append(i) or {})
    assert labels == {} and asked == [] and pending == {}


def test_an_unanswered_item_keeps_jevs_answer_for_the_next_pass():
    items = items_for("Remove the prefix rule")
    unclear = jev_answer({"scope": .46, "teach": .32, "none": .22})
    luna_down = {"#2": {"batch": 0, "raw": None, "error": "codex exited 1", "latency_ms": 1}}
    labels, _runs, pending = fb.classify(items, KINDS, key="k", ask_jev=lambda s: {"#2": unclear},
                                         ask_luna=lambda i: luna_down)
    assert labels == {} and pending["#2"]["main"] == "scope"


def test_a_kept_jev_answer_is_not_asked_again():
    items = items_for("Remove the prefix rule")
    items["#2"]["jev"] = fb.read_jev(jev_answer({"scope": .46, "teach": .32, "none": .22}))
    luna = {"#2": {"batch": 0, "raw": {"id": "#2", "feedback": ["scope"], "confidence": .8},
                   "error": None, "latency_ms": 1}}

    def no_jev(states):
        raise AssertionError("Jev asked again")
    labels, runs, _pending = fb.classify(items, KINDS, key="k", ask_jev=no_jev, ask_luna=lambda i: luna)
    assert labels["#2"]["codes"] == ["scope"] and [r["engine"] for r in runs] == ["llm"]


def test_agreement_uses_the_main_label_else_any_label_else_none():
    assert fb.agrees("defect", ["defect", "scope"], "defect")
    assert not fb.agrees("defect", ["defect", "scope"], "scope")
    assert fb.agrees(None, ["defect", "scope"], "scope")
    assert fb.agrees(None, [], fb.NONE) and not fb.agrees(None, [], "scope")
    family = {"defect": "wrong", "check": "wrong", "scope": "revise"}
    assert fb.agrees_family("defect", ["defect"], "check", family)
    assert fb.agrees_family(None, ["defect", "scope"], "scope", family)
    assert not fb.agrees_family(None, [], "scope", family)
