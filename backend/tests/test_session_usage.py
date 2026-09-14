"""The per-session usage reading behind the transcript's instruments rail.

Tokens and cost are not in the .jsonl; they are in the ledger. Without this
endpoint the reader has to load the whole logbook to see four numbers.
"""
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch, rows):
    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        'subscription_monthly_usd = 200.0\n'
        f'projects_dir = "{proj.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))

    from flightdeck import db
    conn = db.connect(str(db_path))
    for r in rows:
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()

    from flightdeck import server
    return server.create_app()


def test_usage_returns_the_row_for_one_session(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [
        ("u1", "sess-a", "/p", "claude-opus-5", "2026-07-02T00:00:00Z", 10, 20, 30, 0, 5, "standard"),
        ("u2", "sess-a", "/p", "claude-opus-5", "2026-07-02T01:00:00Z", 10, 20, 30, 0, 5, "standard"),
        ("u3", "sess-b", "/p", "claude-opus-5", "2026-07-02T02:00:00Z", 99, 99, 99, 0, 9, "standard"),
    ])
    with TestClient(app) as client:
        r = client.get("/api/session/sess-a/usage")
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == "sess-a"
        assert body["turns"] == 2
        # columns are (input, cache_read, cache_create_5m, cache_create_1h, output)
        assert body["output"] == 10          # 2 rows x 5, and none of sess-b's
        assert body["context"] == 120        # (10 + 20 + 30) x 2
        assert body["cost"] >= 0


def test_usage_404s_for_a_session_with_no_ledger_row(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [
        ("u1", "sess-a", "/p", "claude-opus-5", "2026-07-02T00:00:00Z", 10, 20, 30, 0, 5, "standard"),
    ])
    with TestClient(app) as client:
        assert client.get("/api/session/nope/usage").status_code == 404
