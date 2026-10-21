"""Billed-row dedupe, turns and agent busy time, all read in one ingest pass.

Claude Code writes one jsonl line per content block of an API response, and every
line repeats the response's usage (an early line may carry a partial output
count). Keying the ledger on the line uuid counted each response two to three
times. These tests pin the response key, the resume/fork copy case, the in-place
fix for a ledger written before the key existed, and the busy-time rules.
"""
import json

import pytest

from flightdeck import activity, db, ingest

T0 = "2026-09-10T10:00:00.000Z"


def _ts(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _assistant(uuid, secs, msg_id, out, session="s1", blocks=("tool_use",), stop=None,
               tool="Bash", req="r1", agent=None, prompt=None):
    content = []
    for b in blocks:
        if b == "tool_use":
            content.append({"type": "tool_use", "id": f"t-{uuid}", "name": tool, "input": {}})
        else:
            content.append({"type": b, "text": f"text {uuid}"})
    o = {"type": "assistant", "uuid": uuid, "sessionId": session, "cwd": "/p",
         "timestamp": _ts(secs), "requestId": req,
         "message": {"id": msg_id, "model": "claude-opus-5", "content": content,
                     "stop_reason": stop,
                     "usage": {"input_tokens": 1, "cache_read_input_tokens": 1000,
                               "output_tokens": out, "service_tier": "standard",
                               "speed": "standard", "inference_geo": "global",
                               "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                  "ephemeral_1h_input_tokens": 50}}}}
    if agent:
        o["agentId"] = agent
        o["isSidechain"] = True
    if prompt:
        o["promptId"] = prompt
    return o


def _prompt(uuid, secs, text="fix the bug", session="s1", prompt_id="p1"):
    return {"type": "user", "uuid": uuid, "sessionId": session, "cwd": "/p",
            "timestamp": _ts(secs), "promptId": prompt_id, "turnOrigin": "human",
            "message": {"role": "user", "content": text}}


def _result(uuid, secs, session="s1", prompt_id="p1", agent=None):
    o = {"type": "user", "uuid": uuid, "sessionId": session, "timestamp": _ts(secs),
         "promptId": prompt_id,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}
    if agent:
        o["agentId"] = agent
        o["isSidechain"] = True
    return o


def _write(path, objs, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode) as fh:
        for o in objs:
            fh.write(json.dumps(o) + "\n")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def _billed(conn):
    return conn.execute(
        "SELECT uuid, session_id, output_tokens, prompt_id FROM messages "
        "WHERE dup_of IS NULL ORDER BY ts").fetchall()


def _busy(conn, **where):
    sql = "SELECT COALESCE(SUM(busy_seconds), 0) s FROM agent_activity"
    if where:
        sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
    return conn.execute(sql, tuple(where.values())).fetchone()["s"]


# ------------------------------------------------------------------- dedupe

def test_one_response_split_into_blocks_is_one_billed_row(tmp_path, conn):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [
        _prompt("h1", 0),
        _assistant("a1", 10, "msg_1", 5, blocks=("thinking",)),
        _assistant("a2", 11, "msg_1", 145, blocks=("tool_use",)),
    ])
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    rows = _billed(conn)
    assert len(rows) == 1
    assert rows[0]["output_tokens"] == 145, "the largest output of the response is kept"


def test_a_resumed_session_copy_adds_no_billed_row(tmp_path, conn):
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0), _assistant("a1", 10, "msg_1", 100, stop="end_turn")])
    ingest.ingest_all(conn, str(tmp_path))
    # s2 resumes s1: history copied with new uuids and the new session id.
    _write(tmp_path / "proj" / "s2.jsonl", [
        _prompt("h1-copy", 0, session="s2"),
        _assistant("a1-copy", 10, "msg_1", 100, session="s2", stop="end_turn"),
        _prompt("h2", 4000, text="next step", session="s2", prompt_id="p2"),
        _assistant("a3", 4060, "msg_2", 50, session="s2", stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    rows = _billed(conn)
    assert [r["uuid"] for r in rows] == ["a1", "a3"]
    # The copied turn stays with the session that did the work.
    assert conn.execute("SELECT session_id FROM turns WHERE turn_id='p1'").fetchone()[0] == "s1"
    assert _busy(conn, session_id="s2") == pytest.approx(60.0), "copied history is not counted again"


def test_a_ledger_written_before_the_key_is_fixed_in_place(tmp_path, conn):
    # Two rows stored by the old ingest: one per content block, no message id.
    cols = ("uuid, session_id, project, model, ts, input_tokens, cache_read, "
            "cache_create_5m, cache_create_1h, output_tokens, service_tier")
    for uuid, secs, out in (("a1", 10, 5), ("a2", 11, 145)):
        conn.execute(f"INSERT INTO messages ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (uuid, "s1", "/p", "claude-opus-5", _ts(secs), 1, 1000, 0, 50, out, "standard"))
    conn.commit()
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0),
        _assistant("a1", 10, "msg_1", 5, blocks=("thinking",)),
        _assistant("a2", 11, "msg_1", 145)])
    ingest.ingest_all(conn, str(tmp_path))
    rows = _billed(conn)
    assert [(r["uuid"], r["output_tokens"]) for r in rows] == [("a1", 145)]
    dup = conn.execute("SELECT dup_of, message_id FROM messages WHERE uuid='a2'").fetchone()
    assert (dup["dup_of"], dup["message_id"]) == ("a1", "msg_1"), "marked, not deleted"


# ---------------------------------------------------------------- turns

def test_a_turn_records_its_prompt_end_and_billed_rows(tmp_path, conn):
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0, text="add a chart"),
        _assistant("a1", 10, "msg_1", 20),
        _result("r1", 20),
        _assistant("a2", 30, "msg_2", 40, blocks=("text",), stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    t = conn.execute("SELECT * FROM turns WHERE turn_id='p1'").fetchone()
    assert t["prompt_head"] == "add a chart"
    assert t["ended_at"] == _ts(30)
    assert t["final_head"] == "text a2"
    assert {r["prompt_id"] for r in _billed(conn)} == {"p1"}


def test_machine_lines_do_not_open_a_turn():
    base = {"type": "user", "sessionId": "s1", "timestamp": T0, "promptId": "x"}
    for text in ("<command-name>/usage</command-name>", "[Request interrupted by user]",
                 "Caveat: The messages below", "This session is being continued from a "
                 "previous conversation that ran out of context."):
        assert not activity.is_human_prompt({**base, "message": {"content": text}})
    assert activity.is_human_prompt({**base, "message": {"content": "hello"}})
    assert not activity.is_human_prompt({**base, "turnOrigin": "task_notification",
                                         "message": {"content": "hello"}})


def test_a_prompt_behind_ide_context_opens_a_turn(tmp_path, conn):
    # Older VS Code lines: no turnOrigin, the open file comes first as its own block.
    ide = {"type": "user", "uuid": "h1", "sessionId": "s1", "cwd": "/p", "promptId": "p1",
           "timestamp": _ts(0), "message": {"role": "user", "content": [
               {"type": "text", "text": "<ide_opened_file>The user opened a.py</ide_opened_file>"},
               {"type": "text", "text": "why does this fail?"}]}}
    assert activity.is_human_prompt(ide)
    only_ide = {**ide, "message": {"content": [ide["message"]["content"][0]]}}
    assert not activity.is_human_prompt(only_ide)
    _write(tmp_path / "proj" / "s1.jsonl", [
        ide, _assistant("a1", 30, "msg_1", 1, blocks=("text",), stop="end_turn")])
    ingest.ingest_all(conn, str(tmp_path))
    assert conn.execute("SELECT prompt_head FROM turns WHERE turn_id='p1'").fetchone()[0] \
        == "why does this fail?"
    assert _busy(conn, prompt_id="p1") == pytest.approx(30.0)


def test_a_rescan_refreshes_the_turn_and_its_billed_rows(tmp_path, conn):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [_prompt("h1", 0, text="<ide_selection>x</ide_selection>\nrename it"),
               _assistant("a1", 30, "msg_1", 1, blocks=("text",), stop="end_turn")])
    ingest.ingest_all(conn, str(tmp_path))
    # State left by an older ingest: the tag in the head, the row unstamped.
    conn.execute("UPDATE turns SET prompt_head='<ide_selection>x' WHERE turn_id='p1'")
    conn.execute("UPDATE messages SET prompt_id=NULL")
    conn.execute("DELETE FROM files")
    conn.commit()
    ingest._stat_cache.clear()
    ingest.ingest_all(conn, str(tmp_path))
    assert conn.execute("SELECT prompt_head FROM turns WHERE turn_id='p1'").fetchone()[0] == "rename it"
    assert {r["prompt_id"] for r in _billed(conn)} == {"p1"}


def test_a_prompt_sent_before_the_reply_joins_the_open_turn(tmp_path, conn):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [
        _prompt("h1", 0, text="convert the tools to English"),
        _prompt("h2", 14, text="and keep the old names as aliases", prompt_id="p2"),
        _assistant("a1", 20, "msg_1", 1),
        _result("r1", 30, prompt_id="p2"),
        _assistant("a2", 40, "msg_2", 1, blocks=("text",), stop="end_turn"),
        # After the reply, a new prompt is a new turn.
        _prompt("h3", 100, text="now commit", prompt_id="p3"),
        _assistant("a3", 110, "msg_3", 1, blocks=("text",), stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    heads = dict(conn.execute("SELECT turn_id, prompt_head FROM turns").fetchall())
    assert heads == {"p1": "convert the tools to English\n\nand keep the old names as aliases",
                     "p3": "now commit"}
    assert {r["uuid"]: r["prompt_id"] for r in _billed(conn)} == {"a1": "p1", "a2": "p1", "a3": "p3"}
    assert _busy(conn, prompt_id="p1") == pytest.approx(40.0)
    # A full re-read gives the same turn, not a doubled head.
    conn.execute("DELETE FROM files")
    conn.commit()
    ingest._stat_cache.clear()
    ingest.ingest_all(conn, str(tmp_path))
    assert dict(conn.execute("SELECT turn_id, prompt_head FROM turns").fetchall()) == heads


def test_a_replayed_stretch_keeps_its_turn_and_adds_no_time(tmp_path, conn):
    # The CLI re-appended turn p1's lines (same uuids, old timestamps) inside p2.
    first = [_prompt("h1", 0, text="first goal"),
             _assistant("a1", 60, "msg_1", 1, blocks=("text",), stop="end_turn")]
    _write(tmp_path / "proj" / "s1.jsonl", first + [
        _prompt("h2", 3600, text="second goal", prompt_id="p2"),
        *first,
        _assistant("a2", 3660, "msg_2", 1, blocks=("text",), stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    assert {r["uuid"]: r["prompt_id"] for r in _billed(conn)} == {"a1": "p1", "a2": "p2"}
    assert _busy(conn, prompt_id="p1") == pytest.approx(60.0)
    assert _busy(conn, prompt_id="p2") == pytest.approx(60.0)


# ------------------------------------------------------------ busy time

def test_busy_time_runs_from_prompt_to_end_turn(tmp_path, conn):
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0), _assistant("a1", 60, "msg_1", 1), _result("r1", 120),
        _assistant("a2", 180, "msg_2", 1, blocks=("text",), stop="end_turn"),
        # Idle after end_turn is not work, however long.
        _prompt("h2", 7200, prompt_id="p2"),
        _assistant("a3", 7230, "msg_3", 1, blocks=("text",), stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    assert _busy(conn, prompt_id="p1") == pytest.approx(180.0)
    assert _busy(conn, prompt_id="p2") == pytest.approx(30.0)


def test_a_long_gap_inside_a_turn_is_capped(tmp_path, conn, monkeypatch):
    monkeypatch.setattr(ingest, "_IDLE_CAP_SECONDS", 900.0)
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0), _assistant("a1", 60, "msg_1", 1),
        _result("r1", 60 + 3600),     # an hour-long tool call, or someone away
        _assistant("a2", 3700, "msg_2", 1, blocks=("text",), stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    assert _busy(conn) == pytest.approx(60 + 900 + 40)


def test_waiting_for_an_answer_is_not_busy_time(tmp_path, conn):
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0),
        _assistant("a1", 30, "msg_1", 1, tool="AskUserQuestion"),
        _result("r1", 330),           # five minutes to answer
        _assistant("a2", 360, "msg_2", 1, blocks=("text",), stop="end_turn"),
    ])
    ingest.ingest_all(conn, str(tmp_path))
    assert _busy(conn) == pytest.approx(30 + 30)


def test_subagents_add_their_own_time_to_the_parent_turn(tmp_path, conn):
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0),
        _assistant("a1", 10, "msg_1", 1, tool="Agent"),
        _result("r1", 610),
        _assistant("a2", 620, "msg_2", 1, blocks=("text",), stop="end_turn"),
    ])
    for n in (1, 2):
        _write(tmp_path / "proj" / "s1" / "subagents" / f"agent-sub{n}.jsonl", [
            {**_result(f"d{n}", 12, prompt_id="p1", agent=f"sub{n}"),
             "message": {"role": "user", "content": "dispatch"}},
            _assistant(f"x{n}", 300, f"msg_s{n}", 1, agent=f"sub{n}", prompt="p1"),
            _assistant(f"y{n}", 600, f"msg_t{n}", 1, blocks=("text",), agent=f"sub{n}",
                       prompt="p1", stop="end_turn"),
        ])
    ingest.ingest_all(conn, str(tmp_path))
    assert _busy(conn, agent_id="") == pytest.approx(620.0), "the main agent waits, and counts"
    assert _busy(conn, agent_id="sub1") == pytest.approx(588.0)
    assert _busy(conn, prompt_id="p1") == pytest.approx(620 + 2 * 588)
    sub_rows = conn.execute("SELECT prompt_id, agent_id FROM messages "
                            "WHERE agent_id IS NOT NULL").fetchall()
    assert {(r["prompt_id"], r["agent_id"]) for r in sub_rows} == {("p1", "sub1"), ("p1", "sub2")}


def test_busy_time_is_split_at_utc_midnight(tmp_path, conn):
    late = {"type": "user", "uuid": "h1", "sessionId": "s1", "promptId": "p1",
            "turnOrigin": "human", "timestamp": "2026-09-10T23:59:00.000Z",
            "message": {"content": "late work"}}
    end = _assistant("a1", 0, "msg_1", 1, blocks=("text",), stop="end_turn")
    end["timestamp"] = "2026-09-11T00:01:00.000Z"
    _write(tmp_path / "proj" / "s1.jsonl", [late, end])
    ingest.ingest_all(conn, str(tmp_path))
    by_day = dict(conn.execute("SELECT day, SUM(busy_seconds) FROM agent_activity GROUP BY day").fetchall())
    assert by_day == {"2026-09-10": pytest.approx(60.0), "2026-09-11": pytest.approx(60.0)}


def test_an_incremental_read_continues_the_open_turn(tmp_path, conn):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [_prompt("h1", 0), _assistant("a1", 60, "msg_1", 1)])
    ingest.ingest_all(conn, str(tmp_path))
    ingest._stat_cache.clear()
    _write(f, [_result("r1", 120),
               _assistant("a2", 180, "msg_2", 1, blocks=("text",), stop="end_turn")], mode="a")
    ingest.ingest_all(conn, str(tmp_path))
    assert _busy(conn) == pytest.approx(180.0)


def test_a_full_rescan_does_not_double_the_busy_time(tmp_path, conn):
    _write(tmp_path / "proj" / "s1.jsonl", [
        _prompt("h1", 0), _assistant("a1", 90, "msg_1", 1, blocks=("text",), stop="end_turn")])
    ingest.ingest_all(conn, str(tmp_path))
    conn.execute("DELETE FROM files")
    conn.commit()
    ingest._stat_cache.clear()
    ingest.ingest_all(conn, str(tmp_path))
    assert _busy(conn) == pytest.approx(90.0)
