from flightdeck import db


def test_connect_creates_tables(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"messages", "files"} <= names


def test_messages_upsert_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    row = ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-02T00:00:00Z",
           10, 20, 30, 0, 5, "standard")
    sql = ("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(uuid) DO NOTHING")
    conn.execute(sql, row)
    conn.execute(sql, row)  # duplicate
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 1
