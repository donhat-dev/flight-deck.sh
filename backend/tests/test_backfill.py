"""The repair of history whose transcripts are gone.

Those rows have no message id, so repeats are matched on the input-side token
tuple: within a session first (block repeats differ in timestamp), then across
sessions at the same timestamp (resume/fork copies).
"""
import pytest

from flightdeck import backfill, db

COLS = ("uuid, session_id, project, model, ts, input_tokens, cache_read, "
        "cache_create_5m, cache_create_1h, output_tokens, service_tier, message_id")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def _put(conn, *rows):
    for r in rows:
        conn.execute(f"INSERT INTO messages ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r)
    conn.commit()


def _billed(conn):
    return {r["uuid"]: r["output_tokens"] for r in conn.execute(
        "SELECT uuid, output_tokens FROM messages WHERE dup_of IS NULL")}


def test_block_repeats_and_copies_collapse_to_one_billed_row(conn):
    big = (1, 50_000, 0, 200)
    _put(conn,
         ("a1", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:00.000Z", *big, 5, "standard", None),
         ("a2", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:01.000Z", *big, 145, "standard", None),
         # s2 is a fork of s1: the same response, same timestamp, new session.
         ("c1", "s2", "/p", "claude-opus-5", "2026-07-20T10:00:00.000Z", *big, 5, "standard", None),
         ("c2", "s2", "/p", "claude-opus-5", "2026-07-20T10:00:01.000Z", *big, 145, "standard", None),
         # A different response in s1.
         ("b1", "s1", "/p", "claude-opus-5", "2026-07-20T10:05:00.000Z", 1, 51_000, 0, 30, 70, "standard", None))
    got = backfill.collapse_heuristic(conn, apply=True)
    assert _billed(conn) == {"a1": 145, "b1": 70}
    assert got["rows_marked"] == 3
    assert {r["uuid"]: r["dup_of"] for r in conn.execute(
        "SELECT uuid, dup_of FROM messages WHERE dup_of IS NOT NULL")} == {
            "a2": "a1", "c1": "a1", "c2": "a1"}


def test_small_tuples_are_not_matched_across_sessions(conn):
    small = (10, 0, 0, 0)   # a probe call repeats exactly in every session
    _put(conn,
         ("p1", "s1", "/p", "claude-haiku-4-5", "2026-07-20T10:00:00.000Z", *small, 3, "standard", None),
         ("p2", "s2", "/p", "claude-haiku-4-5", "2026-07-20T10:00:00.000Z", *small, 3, "standard", None))
    backfill.collapse_heuristic(conn, apply=True)
    assert set(_billed(conn)) == {"p1", "p2"}


def test_two_different_message_ids_never_merge(conn):
    same = (1, 50_000, 0, 200)
    _put(conn,
         ("m1", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:00.000Z", *same, 9, "standard", "msg_a"),
         ("m2", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:09.000Z", *same, 9, "standard", "msg_b"),
         ("n1", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:10.000Z", *same, 12, "standard", None))
    backfill.collapse_heuristic(conn, apply=True)
    billed = _billed(conn)
    assert set(billed) == {"m1", "m2"}
    assert billed["m1"] == 12, "the id-less repeat folds into the row that has an id"


def test_a_dry_run_changes_nothing(conn):
    big = (1, 50_000, 0, 200)
    _put(conn,
         ("a1", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:00.000Z", *big, 5, "standard", None),
         ("a2", "s1", "/p", "claude-opus-5", "2026-07-20T10:00:01.000Z", *big, 145, "standard", None))
    assert backfill.collapse_heuristic(conn, apply=False)["rows_marked"] == 1
    assert set(_billed(conn)) == {"a1", "a2"}


def test_gone_sessions_get_a_ledger_estimate(conn):
    _put(conn,
         ("a1", "gone", "/p", "claude-opus-5", "2026-07-20T10:01:00.000Z", 1, 20_000, 0, 0, 5, "standard", None),
         ("a2", "gone", "/p", "claude-opus-5", "2026-07-20T10:03:00.000Z", 1, 21_000, 0, 0, 5, "standard", None))
    conn.execute("INSERT INTO message_text (uuid, session_id, role, ts, seq, text) "
                 "VALUES ('u1', 'gone', 'user', '2026-07-20T10:00:00.000Z', 1, 'do the thing')")
    conn.execute("INSERT INTO message_text (uuid, session_id, role, ts, seq, text) "
                 "VALUES ('u2', 'gone', 'user', '2026-07-20T11:00:00.000Z', 2, '<command-name>/usage')")
    conn.commit()
    got = backfill.estimate_gone_sessions(conn, cap_seconds=900, apply=True)
    assert got["sessions"] == 1
    row = conn.execute("SELECT day, method, busy_seconds FROM agent_activity").fetchone()
    assert (row["day"], row["method"]) == ("2026-07-20", "ledger")
    assert row["busy_seconds"] == pytest.approx(180.0)
