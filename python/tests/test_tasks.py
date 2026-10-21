"""Grouping turns into tasks, and cost per task.

The remote stages (Jev, codex) are replaced by fakes; these tests pin the rules,
the acceptance gate, the id checks on LLM answers, parent resolution, the human
label's precedence and the per-task rollup.
"""
import pytest

from flightdeck import db, tasks


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def _turn(conn, tid, start, end=None, session="s1", prompt="build the export",
          cwd="/home/p", final="done", billed=1):
    conn.execute(
        "INSERT INTO turns (turn_id, session_id, started_at, ended_at, prompt_head, final_head, "
        "cwd, git_branch) VALUES (?,?,?,?,?,?,?,?)",
        (tid, session, f"2026-09-20T{start}:00.000Z",
         f"2026-09-20T{end}:00.000Z" if end else None, prompt, final, cwd, "main"))
    for n in range(billed):
        conn.execute(
            "INSERT INTO messages (uuid, session_id, project, model, ts, input_tokens, cache_read, "
            "cache_create_5m, cache_create_1h, output_tokens, service_tier, prompt_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{tid}-m{n}", session, "/p", "claude-opus-5-5", f"2026-09-20T{start}:30.000Z",
             1000, 0, 0, 0, 1000, "standard", tid))
    conn.commit()


def _labels(conn):
    return {r["turn_id"]: dict(r) for r in conn.execute(
        "SELECT turn_id, kind, parent_id, merged_ids, label_source, needs_review FROM turns")}


def test_rules_label_scripted_empty_and_ack_turns(conn):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "10:25", "10:40", prompt="ok, go ahead")
    _turn(conn, "t3", "11:00", "11:01", prompt="/usage", billed=0)
    _turn(conn, "t4", "11:05", "11:06", session="probe", cwd="/tmp", prompt="reply ok")
    stats = tasks.label_pending(conn, remote=False)
    got = _labels(conn)
    assert (got["t2"]["kind"], got["t2"]["parent_id"]) == ("subtask", "t1")
    assert got["t3"]["kind"] == "empty"
    assert got["t4"]["kind"] == "automated"
    assert got["t1"]["kind"] is None, "no rule fits; left for the remote stages"
    assert stats["labeled"] == 3


def test_an_ack_after_a_long_break_is_not_a_rule_subtask(conn):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "11:30", "11:40", prompt="ok")
    tasks.label_pending(conn, remote=False)
    assert _labels(conn)["t2"]["kind"] is None


def test_a_clear_jev_answer_is_kept_and_a_flat_one_escalates(conn, monkeypatch):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "10:30", "10:50", prompt="now add a CSV option to the export")
    _turn(conn, "t3", "11:00", "11:20", prompt="something ambiguous and long enough")
    answers = {
        "t1": {"kind": "task", "p1": 0.9, "margin": 0.85, "probs": {}},
        "t2": {"kind": "subtask", "p1": 0.8, "margin": 0.65, "probs": {}},
        "t3": {"kind": "task", "p1": 0.45, "margin": 0.1, "probs": {}},
    }
    monkeypatch.setattr(tasks, "jev_key", lambda: "k")
    monkeypatch.setattr(tasks, "jev_classify",
                        lambda state, key: answers["t3" if "ambiguous" in state.split("NEW PROMPT")[1]
                                                  else "t2" if "CSV" in state.split("NEW PROMPT")[1]
                                                  else "t1"])
    sent = []

    def fake_llm(items):
        sent.extend(i["turn_id"] for i in items)
        return {"t3": {"turn_id": "t3", "kind": "subtask", "parent_id": "t2",
                       "merged_ids": [], "title": "", "confidence": 0.9}}
    monkeypatch.setattr(tasks, "llm_classify", fake_llm)
    tasks.label_pending(conn)
    got = _labels(conn)
    assert (got["t1"]["kind"], got["t1"]["label_source"]) == ("task", "jev")
    assert (got["t2"]["kind"], got["t2"]["parent_id"]) == ("subtask", "t1")
    assert sent == ["t3"]
    # t3 continues t2, whose root is t1.
    assert (got["t3"]["kind"], got["t3"]["parent_id"], got["t3"]["label_source"]) == \
        ("subtask", "t1", "llm")


def test_llm_ids_outside_the_offered_candidates_go_to_review(conn, monkeypatch):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "10:30", "10:50", prompt="commit everything we did today please")
    monkeypatch.setattr(tasks, "jev_key", lambda: "k")
    monkeypatch.setattr(tasks, "jev_classify", lambda state, key: {
        "kind": "merge", "p1": 0.9, "margin": 0.8, "probs": {}})
    monkeypatch.setattr(tasks, "llm_classify", lambda items: {i["turn_id"]: {
        "turn_id": i["turn_id"], "kind": "merge", "parent_id": "",
        "merged_ids": ["t1", "made-up"], "title": "Commit", "confidence": 0.95} for i in items})
    tasks.label_pending(conn)
    t2 = _labels(conn)["t2"]
    assert t2["kind"] == "merge"
    assert t2["merged_ids"] == '["t1"]'
    assert t2["needs_review"] == 1, "a merge of fewer than two offered tasks is reviewed"


def test_a_subtask_skips_an_empty_turn_to_find_its_task(conn):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "10:25", "10:26", prompt="/compact", billed=0)
    _turn(conn, "t3", "10:30", "10:40", prompt="continue")
    _turn(conn, "u1", "12:00", "12:01", session="s2", prompt="interrupted at once", billed=0)
    _turn(conn, "u2", "12:02", "12:30", session="s2", prompt="go on")
    tasks.set_label(conn, "t1", "task")
    tasks.label_pending(conn, remote=False)
    got = _labels(conn)
    assert (got["t3"]["kind"], got["t3"]["parent_id"]) == ("subtask", "t1")
    assert (got["u2"]["kind"], got["u2"]["parent_id"]) == ("subtask", None)
    ids = {r["task_id"] for r in tasks.rollup(conn)["tasks"]
           if r["kind"] not in ("empty", "automated")}
    assert ids == {"t1", "u2"}, "a subtask with no work before it is its own task"


def test_a_human_label_wins_and_is_never_relabeled(conn):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "10:25", "10:40", prompt="ok")
    assert tasks.set_label(conn, "t2", "task", title="Separate goal")
    conn.execute("UPDATE turns SET labeled_at=NULL WHERE turn_id='t2'")
    conn.commit()
    tasks.label_pending(conn, remote=False)
    got = _labels(conn)["t2"]
    assert (got["kind"], got["label_source"]) == ("task", "human")
    with pytest.raises(ValueError):
        tasks.set_label(conn, "t2", "epic")


def test_a_one_click_subtask_joins_the_previous_task(conn):
    _turn(conn, "t1", "10:00", "10:20")
    _turn(conn, "t2", "10:25", "10:26", prompt="/compact", billed=0)
    _turn(conn, "t3", "11:30", "11:50", prompt="a longer follow-up prompt here")
    tasks.set_label(conn, "t1", "task")
    tasks.label_pending(conn, remote=False)
    assert tasks.set_label(conn, "t3", "subtask")
    assert _labels(conn)["t3"]["parent_id"] == "t1"
    assert not tasks.set_label(conn, "missing", "task")


def test_rollup_sums_subtasks_into_their_task(conn):
    _turn(conn, "t1", "10:00", "10:20", billed=2)
    _turn(conn, "t2", "10:25", "10:40", prompt="ok", billed=1)
    _turn(conn, "t3", "12:00", "12:30", prompt="unrelated work", billed=1)
    for tid, sec in (("t1", 600), ("t2", 300), ("t3", 60)):
        conn.execute("INSERT INTO agent_activity (session_id, agent_id, prompt_id, day, method, "
                     "busy_seconds) VALUES ('s1', '', ?, '2026-09-20', 'transcript', ?)", (tid, sec))
    conn.commit()
    tasks.label_pending(conn, remote=False)
    tasks.set_label(conn, "t1", "task")
    out = tasks.rollup(conn)
    by_id = {r["task_id"]: r for r in out["tasks"]}
    one = 1000 * 4.0 / 1e6 + 1000 * 20.0 / 1e6      # opus 5.5 list price per row
    assert set(by_id) == {"t1", "t3"}
    assert by_id["t1"]["turns"] == 2
    assert by_id["t1"]["cost"] == pytest.approx(3 * one, abs=1e-4)
    assert by_id["t1"]["cost_own"] == pytest.approx(2 * one, abs=1e-4)
    assert by_id["t1"]["agent_hours"] == pytest.approx(900 / 3600, abs=1e-3)
    assert out["summary"]["tasks"] == 2


def test_redact_masks_credentials_before_sending():
    text = ("curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123' "
            "postgresql://fd:hunter2@localhost:5442/db password=s3cret sk-abcdefghijklmnopqrstu")
    out = tasks.redact(text)
    for secret in ("abcdefghijklmnopqrstuvwxyz0123", "hunter2", "s3cret", "sk-abcdefghijklmnopqrstu"):
        assert secret not in out
    assert "postgresql://fd:[secret]@localhost" in out
