"""A torn write must not take the ingest down.

One real session file on disk contains a line that is a run of NUL bytes.
`json.loads` on raw bytes sniffs the encoding from the first four, reads that
line as UTF-32 and raises UnicodeDecodeError, which is not a JSONDecodeError.
Before this was handled, that one line stopped ingest for every session and the
app failed to start.
"""
import json


def test_a_nul_run_is_skipped_not_fatal(tmp_path):
    from flightdeck import db, ingest

    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    f = proj / "s.jsonl"
    good = json.dumps({
        "type": "assistant", "uuid": "u1", "sessionId": "s1", "cwd": "/p",
        "timestamp": "2026-07-02T00:00:00Z",
        "message": {"model": "claude-opus-5", "usage": {"input_tokens": 1, "output_tokens": 2}},
    })
    with open(f, "wb") as fh:
        fh.write(good.encode() + b"\n")
        fh.write(b"\x00" * 64 + b"\n")
        fh.write(good.replace('"u1"', '"u2"').encode() + b"\n")

    conn = db.connect(str(tmp_path / "audit.db"))
    ingest.ingest_file(conn, str(f))
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    assert rows == 2      # both good lines landed, the torn one was skipped
