import os
from fastapi.testclient import TestClient


def test_summary_endpoint(tmp_path, monkeypatch):
    # point the app at a temp config + seeded db
    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        f'subscription_monthly_usd = 200.0\n'
        f'projects_dir = "{proj.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))

    from flightdeck import server
    app = server.create_app()
    with TestClient(app) as client:
        r = client.get("/api/summary")
        assert r.status_code == 200
        body = r.json()
        assert "total_cost" in body and "cache_hit_rate" in body


def test_daily_endpoint_uses_per_request_connection(tmp_path, monkeypatch):
    # A read endpoint must work against a seeded DB with the fresh
    # per-request connection design (no shared long-lived read connection).
    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        f'subscription_monthly_usd = 200.0\n'
        f'projects_dir = "{proj.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))

    # Seed a row directly so /api/daily has data to aggregate.
    from flightdeck import db
    conn = db.connect(str(db_path))
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-02T00:00:00Z",
         10, 20, 30, 0, 5, "standard"))
    conn.commit()
    conn.close()

    from flightdeck import server
    app = server.create_app()
    with TestClient(app) as client:
        r = client.get("/api/daily")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert any(d["date"] == "2026-07-02" for d in body)
