"""The FastAPI app answers every shared route with a body valid against
spend.yaml — the same routes `fd serve` serves.

Fixture mirrors python/tests/test_server.py plus the hermetic env rules: a
temporary config.toml points db_path/projects_dir under tmp_path, and every
env override the app can read is pinned under the same tmp_path or deleted —
config.load() gives TOKEN_AUDIT_DB_PATH / TOKEN_AUDIT_PROJECTS_DIR (and the
other overrides in envpins.py) precedence over the TOML, so an inherited
value would otherwise redirect the ledger or the transcript sweep onto real
paths. db's engine seam is reset before `create_app()`.

The lifespan runs but every background task is isolated or off:
TOKEN_AUDIT_WATCH=0 (no transcript watcher), TOKEN_AUDIT_REINGEST_
INTERVAL=0 (no periodic reingest), the poll and task-label loops are off by
default (their interval envs cleared), and TREASURES_STORE points at
tmp_path so the filestore watcher observes a throwaway dir.
`rt.reingest()` still runs once at startup — it reads the empty fixture
projects dir and warms the snapshot cache the routes answer from, which is
the production read path.
"""
import pytest
import yaml
from fastapi.testclient import TestClient

import envpins
from conftest import (ROOT, SPEND_YAML, operation_schema, shared_paths,
                      validate)

_SPEND = yaml.safe_load(SPEND_YAML.read_text())


def _make_client(tmp_path, monkeypatch):
    """The app under a hermetic env: every env override the app reads is
    pinned under tmp_path or deleted (envpins.py), then create_app() runs
    against the temporary TOML. Returns an un-entered TestClient."""
    for key in envpins.CLEAR:
        monkeypatch.delenv(key, raising=False)
    for key, val in envpins.env_map(tmp_path).items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv("TOKEN_AUDIT_WATCH", "0")
    monkeypatch.setenv("TOKEN_AUDIT_REINGEST_INTERVAL", "0")

    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
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
    return TestClient(server.create_app())


@pytest.fixture()
def client(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch) as c:
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


# --- the env-pinning guarantee ------------------------------------------------

def test_env_pin_covers_every_override():
    """Every TOKEN_AUDIT_*/FLIGHTDECK_*/TREASURES_* name the package reads is
    classified in envpins.py — a new override fails here until it is pinned
    or cleared, which is what keeps the fixture hermetic."""
    unclassified = (envpins.discovered(ROOT)
                    - set(envpins.PIN) - set(envpins.CLEAR)
                    - set(envpins.FIXTURE_VARS))
    assert not unclassified, (
        f"classify these env overrides in envpins.py (PIN or CLEAR): "
        f"{sorted(unclassified)}")


def test_env_overrides_cannot_redirect_paths(tmp_path, monkeypatch):
    """Negative: pre-seed every pinnable override at a sentinel directory,
    then build the client — pinning must win and the sentinel stay untouched.
    (Unpinned, e.g. TOKEN_AUDIT_DB_PATH would have db.connect create the
    ledger there.)"""
    sentinel = tmp_path / "sentinel"          # deliberately never created
    for name in envpins.PIN:
        monkeypatch.setenv(name, str(sentinel / name.lower()))
    with _make_client(tmp_path / "app", monkeypatch) as c:
        assert c.get("/api/summary").status_code == 200
    assert not sentinel.exists(), (
        f"app used an inherited env path: "
        f"{[str(p) for p in sentinel.rglob('*')]}")
