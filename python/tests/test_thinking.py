"""Thinking time and tokens per API response, read in the ingest pass.

Transcripts carry no thinking text, only `thinking_tokens` in usage and the time
each content block was written. These tests pin the response's start (the record
before its first line, hook output included), the end of thinking, a start that
survives a resumed read, the one-time backfill, and the per-day statistics.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from flightdeck import db, ingest, thinking

BASE = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)


def _ts(secs):
    return thinking.zulu(BASE + timedelta(seconds=secs))


def _line(kind, uuid, secs, msg_id=None, block=None, think=None, effort="xhigh", out=300,
          model="claude-opus-5-5", session="s1", **extra):
    o = {"type": kind, "uuid": uuid, "sessionId": session, "cwd": "/p", "timestamp": _ts(secs),
         **extra}
    if kind == "assistant":
        usage = {"input_tokens": 2, "cache_read_input_tokens": 1000, "output_tokens": out,
                 "service_tier": "standard", "cache_creation": {}}
        if think is not None:
            usage["output_tokens_details"] = {"thinking_tokens": think}
        o.update({"requestId": f"req-{msg_id}", "effort": effort, "message": {
            "id": msg_id, "model": model, "stop_reason": "tool_use", "usage": usage,
            "content": [{"type": "thinking", "thinking": "", "signature": "x"} if block == "thinking"
                        else {"type": "tool_use", "id": uuid, "name": "Bash", "input": {}}]}})
    elif kind == "user":
        o["message"] = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}
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


def _timing(conn):
    return {r["message_id"]: dict(r) for r in conn.execute("SELECT * FROM response_timing")}


def test_response_runs_from_the_record_before_it_to_its_blocks(conn, tmp_path):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [
        {"type": "user", "uuid": "u0", "sessionId": "s1", "timestamp": _ts(0),
         "turnOrigin": "human", "promptId": "p1", "message": {"role": "user", "content": "go"}},
        _line("attachment", "a0", 1),                                 # hook output
        _line("assistant", "m1", 4, "M1", "thinking", think=51),
        _line("assistant", "m2", 5, "M1", "tool_use", think=51),
        _line("user", "r1", 5.3),                                     # streamed tool result
        _line("assistant", "m3", 5.5, "M1", "tool_use", think=51),
        _line("user", "r2", 8),
        _line("assistant", "m4", 9, "M2", "tool_use", think=0, effort="high"),
    ])
    ingest.ingest_file(conn, str(f))
    t = _timing(conn)
    assert (t["M1"]["started_at"], t["M1"]["think_end_at"], t["M1"]["ended_at"]) == (
        _ts(1), _ts(4), _ts(5.5))
    assert (t["M1"]["thinking_tokens"], t["M1"]["effort"], t["M1"]["agent_id"]) == (51, "xhigh", "")
    assert (t["M2"]["started_at"], t["M2"]["think_end_at"], t["M2"]["effort"]) == (_ts(8), None, "high")


def test_a_resumed_read_starts_from_the_saved_last_timestamp(conn, tmp_path):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [_line("assistant", "m1", 0, "M1", "tool_use"), _line("user", "r1", 3)])
    ingest.ingest_file(conn, str(f))
    ingest._stat_cache.clear()
    _write(f, [_line("assistant", "m2", 7, "M2", "thinking", think=90)], mode="a")
    ingest.ingest_file(conn, str(f))
    assert (_timing(conn)["M2"]["started_at"], _timing(conn)["M2"]["think_end_at"]) == (_ts(3), _ts(7))


def test_an_empty_timing_table_triggers_one_full_rescan(conn, tmp_path):
    f = tmp_path / "proj" / "s1.jsonl"
    _write(f, [_line("user", "r0", 0), _line("assistant", "m1", 2, "M1", "thinking", think=10)])
    ingest.ingest_file(conn, str(f))
    conn.execute("DELETE FROM response_timing")
    conn.commit()
    assert ingest._maybe_backfill_response_timing(conn) is True
    assert conn.execute("SELECT count(*) n FROM files").fetchone()["n"] == 0
    ingest.ingest_file(conn, str(f))
    assert _timing(conn)["M1"]["started_at"] == _ts(0)
    assert ingest._maybe_backfill_response_timing(conn) is False


def _response(conn, msg, start, think_s, tokens, model="claude-opus-5-5", effort="xhigh", agent=""):
    conn.execute(
        "INSERT INTO response_timing (message_id, request_id, session_id, agent_id, model, effort, "
        "started_at, think_end_at, ended_at, thinking_tokens, output_tokens) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (msg, "r", "s1", agent, model, effort, _ts(start),
         _ts(start + think_s) if think_s is not None else None, _ts(start + 60), tokens, 500))


def test_series_averages_per_day_over_responses_that_thought(conn):
    _response(conn, "a", 0, 4, 100)
    _response(conn, "b", 10, 8, 300)
    _response(conn, "c", 20, None, 0)                                   # no thinking
    _response(conn, "d", 30, 4000, 50)                                  # start not this request's
    _response(conn, "e", 40, 2, 20, effort="low", agent="sub-1")
    _response(conn, "f", 86400, 6, 60, model="claude-fable-5-1")        # next day
    _response(conn, "g", 50, 3, 30, model="gpt-5.6-sol")                # other vendor
    _response(conn, "h", 60, 5, 0)                                      # progress-update block only
    _response(conn, "i", 70, 9, None)                                   # before thinking_tokens existed
    conn.commit()
    out = thinking.series(conn)
    day1, day2 = out["series"]
    assert (day1["date"], day1["responses"], day1["thinking"], day1["timed"]) == ("2026-09-25", 7, 5, 4)
    assert day1["avg_s"] == pytest.approx((4 + 8 + 2 + 9) / 4, abs=0.01)
    assert day1["avg_tokens"] == round((100 + 300 + 50 + 20) / 4)
    assert day1["thinking_share"] == round(5 / 7, 3) and day2["avg_s"] == 6.0
    assert out["filters"] == {"models": ["claude-opus-5-5", "claude-fable-5-1"],
                              "efforts": ["xhigh", "low"]}
    main_xhigh = thinking.series(conn, model="claude-opus-5-5", effort="xhigh", agent="main")
    assert main_xhigh["totals"]["responses"] == 6
    assert main_xhigh["totals"]["avg_s"] == pytest.approx((4 + 8 + 9) / 3, abs=0.01)
    assert thinking.series(conn, agent="sub")["totals"]["avg_s"] == 2.0
