"""The FastAPI app answers every shared route with a body valid against
spend.yaml — the same routes `fd serve` serves.

Fixture mirrors python/tests/test_server.py plus the hermetic env rules: a
temporary config.toml points db_path/projects_dir under tmp_path,
TOKEN_AUDIT_DATABASE_URL is cleared, and db's engine seam is reset before
`create_app()`. The lifespan runs but every background task is isolated or
off: TOKEN_AUDIT_WATCH=0 (no transcript watcher), TOKEN_AUDIT_REINGEST_
INTERVAL=0 (no periodic reingest), the poll and task-label loops are off by
default (their interval envs unset), and TREASURES_STORE points at tmp_path
so the filestore watcher observes a throwaway dir. `rt.reingest()` still runs
once at startup — it reads the empty fixture projects dir and warms the
snapshot cache the routes answer from, which is the production read path.
"""
import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import (SPEND_YAML, operation_schema, shared_paths, validate)

_SPEND = yaml.safe_load(SPEND_YAML.read_text())


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for key in ("TOKEN_AUDIT_DATABASE_URL", "TOKEN_AUDIT_POLL_INTERVAL",
                "TOKEN_AUDIT_POLL_ACTIVE_INTERVAL",
                "TOKEN_AUDIT_TASK_LABEL_INTERVAL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOKEN_AUDIT_WATCH", "0")
    monkeypatch.setenv("TOKEN_AUDIT_REINGEST_INTERVAL", "0")
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "treasures"))

    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    projects = tmp_path / "projects"
    projects.mkdir()
    cfg.write_text(
        "subscription_monthly_usd = 200.0\n"
        f'projects_dir = "{projects.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))

    from flightdeck import db
    monkeypatch.setattr(db, "_URL", None, raising=False)
    monkeypatch.setattr(db, "_POOL", None, raising=False)

    # Seed a few ledger rows the way python/tests does (test_server.py).
    conn = db.connect(str(db_path))
    conn.execute(
        "INSERT INTO messages (uuid, session_id, project, model, ts, "
        "input_tokens, cache_read, cache_create_5m, cache_create_1h, "
        "output_tokens, service_tier, message_id, request_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("u1", "s1", "/p", "claude-opus-5-5", "2026-09-25T07:00:00Z",
         2, 1000, 0, 0, 300, "standard", "M1", "r1"))
    conn.execute(
        "INSERT INTO messages (uuid, session_id, project, model, ts, "
        "input_tokens, cache_read, cache_create_5m, cache_create_1h, "
        "output_tokens, service_tier, message_id, request_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("u2", "s2", "/p", "claude-sonnet-4-5", "2026-09-26T08:00:00Z",
         5, 0, 20, 0, 25, "standard", "M2", "r2"))
    conn.execute("INSERT INTO session_meta (session_id, title) VALUES (?,?)",
                 ("s1", "Fixture session"))
    conn.commit()
    conn.close()

    from flightdeck import server
    app = server.create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", shared_paths(_SPEND, "python"))
def test_shared_route_matches_spend_yaml(client, registry, spend_spec, path):
    schema, _ = operation_schema(spend_spec, path)
    r = client.get(path)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:300]}"
    validate(registry, schema, r.json())


def test_sessions_row_carries_subagents(client):
    # The Python-only key spend.yaml marks optional: FastAPI always sends it.
    rows = client.get("/api/sessions").json()
    assert rows and all("subagents" in r for r in rows)
