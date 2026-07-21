import json
import os
from flightdeck import db, ingest


def _line(uuid, model="claude-opus-4-8", usage=True):
    o = {"type": "assistant", "uuid": uuid, "sessionId": "s1",
         "cwd": "/p", "timestamp": "2026-07-02T00:00:00Z",
         "message": {"model": model}}
    if usage:
        o["message"]["usage"] = {
            "input_tokens": 10, "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30, "output_tokens": 5,
            "service_tier": "standard",
            "cache_creation": {"ephemeral_5m_input_tokens": 30,
                               "ephemeral_1h_input_tokens": 0}}
    return json.dumps(o)


def test_parse_line_extracts_row():
    row = ingest.parse_line(json.loads(_line("u1")))
    assert row["uuid"] == "u1"
    assert row["cache_create_5m"] == 30
    assert row["cache_create_1h"] == 0
    assert row["cache_read"] == 20


def test_parse_line_skips_non_usage():
    assert ingest.parse_line({"type": "user"}) is None
    assert ingest.parse_line(json.loads(_line("u2", usage=False))) is None


def test_incremental_ingest(tmp_path):
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(_line("u1") + "\n")
    conn = db.connect(str(tmp_path / "t.db"))
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    # append one line; only the new one is ingested
    with f.open("a") as fh:
        fh.write(_line("u2") + "\n")
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 2


def test_partial_line_recovered(tmp_path):
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    # one complete line + a partial fragment with NO trailing newline
    f.write_text(_line("u1") + "\n" + '{"type":"assistant","uuid":"u2"')
    conn = db.connect(str(tmp_path / "t.db"))
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 1
    complete_bytes = len((_line("u1") + "\n").encode())
    assert conn.execute(
        "SELECT bytes_ingested b FROM files WHERE path=?", (str(f),)
    ).fetchone()["b"] == complete_bytes
    # the fragment now completes into a full line
    f.write_text(_line("u1") + "\n" + _line("u2") + "\n")
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 2


def test_ai_title_captured(tmp_path):
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    title1 = json.dumps({"type": "ai-title", "sessionId": "s1", "aiTitle": "First"})
    title2 = json.dumps({"type": "ai-title", "sessionId": "s1", "aiTitle": "Refined title"})
    f.write_text(title1 + "\n" + _line("u1") + "\n" + title2 + "\n")
    conn = db.connect(str(tmp_path / "t.db"))
    ingest.ingest_all(conn, str(tmp_path))
    # latest ai-title wins; ai-title lines are not counted as message rows
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 1
    row = conn.execute(
        "SELECT title FROM session_meta WHERE session_id='s1'").fetchone()
    assert row["title"] == "Refined title"


def test_custom_title_wins_over_ai_title(tmp_path):
    # A user-set custom title (or a fork's "Forked: …") must win over ai-title
    # regardless of order — even an ai-title written AFTER must not clobber it.
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    lines = [
        json.dumps({"type": "ai-title", "sessionId": "s1", "aiTitle": "Auto one"}),
        json.dumps({"type": "custom-title", "sessionId": "s1", "customTitle": "My rename"}),
        _line("u1"),
        json.dumps({"type": "ai-title", "sessionId": "s1", "aiTitle": "Auto two"}),
    ]
    f.write_text("\n".join(lines) + "\n")
    conn = db.connect(str(tmp_path / "t.db"))
    ingest.ingest_all(conn, str(tmp_path))
    row = conn.execute(
        "SELECT title, title_source FROM session_meta WHERE session_id='s1'").fetchone()
    assert row["title"] == "My rename"
    assert row["title_source"] == "custom"


def test_fork_title_captured_without_ai_title(tmp_path):
    # Forked sessions carry ONLY a custom-title (no ai-title) — must still show.
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    ct = json.dumps({"type": "custom-title", "sessionId": "s1",
                     "customTitle": "Forked: do the thing"})
    f.write_text(ct + "\n" + _line("u1") + "\n")
    conn = db.connect(str(tmp_path / "t.db"))
    ingest.ingest_all(conn, str(tmp_path))
    row = conn.execute(
        "SELECT title FROM session_meta WHERE session_id='s1'").fetchone()
    assert row["title"] == "Forked: do the thing"


def test_bytes_ingested_advances(tmp_path):
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(_line("u1") + "\n" + _line("u2") + "\n")
    conn = db.connect(str(tmp_path / "t.db"))
    ingest.ingest_all(conn, str(tmp_path))
    assert conn.execute(
        "SELECT bytes_ingested b FROM files WHERE path=?", (str(f),)
    ).fetchone()["b"] == os.path.getsize(str(f))
