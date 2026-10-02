from datetime import datetime, timedelta, timezone

from conftest import load

tc = load("task_classifier")
fb = load("feedback_classifier")
sr = load("step_reader")
T0 = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def pair(prompt, final_before):
    prev = {"id": "p1", "rec": 1, "session": 1, "start": T0, "end": T0 + timedelta(minutes=2),
            "prompt": "Build the page", "final": final_before, "cwd": "/x", "branch": "",
            "calls": 3, "intent": "task"}
    t = {"id": "p2", "rec": 2, "session": 1, "start": T0 + timedelta(minutes=5),
         "end": T0 + timedelta(minutes=8), "prompt": prompt, "final": "", "cwd": "/x",
         "branch": "", "calls": 3, "intent": None}
    return t, {"prev": [prev], "tasks": [prev], "others": [], "gap_min": 3.0, "moved": False}


def test_the_type_values_are_the_specs():
    assert tc.INTENTS == ("task", "follow_up", "ask", "side", "no_work")
    assert tc.CONTINUES == ("follow_up", "ask")
    assert tc.INTENT_KIND == {"task": "task", "follow_up": "subtask", "ask": "subtask",
                              "side": "no_work", "no_work": "no_work"}
    assert set(tc.KIND_QUESTION["criteria"]) == {"task", "follow_up", "ask", "side"}


def test_a_short_reply_to_a_question_is_a_follow_up_and_a_decision():
    t, c = pair("yes", "Done. Shall I add the export?")
    assert tc.rule_label(t, c)["intent"] == "follow_up"
    assert fb.rule_feedback(t, c)["codes"] == ["decision"]


def test_luna_is_asked_for_the_new_values_only():
    enum = tc.llm_schema({})["properties"]["labels"]["items"]["properties"]["intent"]["enum"]
    assert enum == ["task", "follow_up", "ask", "side"]
    assert '"correction"' not in tc.LLM_INSTRUCTIONS and '"answer"' not in tc.LLM_INSTRUCTIONS


def test_the_digest_names_the_next_prompts_feedback_and_keeps_1500_chars():
    text = sr.digest({"id": 1}, [], "reply",
                     {"intent": "follow_up", "feedback": ["defect"], "prompt": "x" * 2000})
    last = text.splitlines()[-1]
    assert last.startswith("Next person turn: follow_up · feedback defect — ")
    assert last.split(" — ", 1)[1] == "x" * 1500


def test_a_follow_up_that_only_asks_to_explain_or_report_is_a_question_about_the_work():
    assert fb.type_from_feedback("follow_up", ["explain"]) == "ask"
    assert fb.type_from_feedback("follow_up", ["explain", "status"]) == "ask"
    # Anything else asked of the agent keeps the follow-up.
    assert fb.type_from_feedback("follow_up", ["explain", "scope"]) is None
    assert fb.type_from_feedback("follow_up", []) is None
    # Only a follow-up moves: a task or a side question stays what it is.
    assert fb.type_from_feedback("task", ["explain"]) is None
    assert fb.type_from_feedback("side", ["status"]) is None


def test_the_type_criteria_keep_explanations_out_of_follow_up():
    assert "not a follow-up" in tc.KIND_QUESTION["criteria"]["follow_up"]
    assert "research" in tc.KIND_QUESTION["criteria"]["ask"]
    assert "not a follow-up" in tc.LLM_INSTRUCTIONS
