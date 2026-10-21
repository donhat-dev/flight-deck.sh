"""Appearance config persists server-side.

This is the difference between a page for trying fonts and a config page: the
choice belongs to the install, not to one browser profile, so it has to survive a
cleared localStorage and reach any client that opens the deck.
"""
import json

import pytest
from fastapi.testclient import TestClient

from flightdeck import db

VALID = {
    "primary": {"font": "satoshi", "weight": 400, "size": 14},
    "label": {"font": "ibm-plex-mono", "weight": 700, "size": 1},
    "mono": {"font": "ibm-plex-mono", "weight": 400, "size": 1},
}


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """Same shape as tests/test_server.py: the app reads a TOML config named by
    TOKEN_AUDIT_CONFIG, and the schema bootstraps when a write connection opens."""
    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        f'subscription_monthly_usd = 200.0\n'
        f'projects_dir = "{proj.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))

    from flightdeck import server
    app = server.create_app()
    with TestClient(app) as client:
        yield client, str(db_path)


@pytest.fixture()
def client(wired):
    return wired[0]


def test_unset_returns_null_so_the_client_keeps_its_defaults(client):
    """The catalogue lives in the frontend, so the server must not invent a
    default it cannot validate."""
    assert client.get("/api/appearance").json() == {"appearance": None}


def test_put_then_get_round_trips(client):
    put = client.put("/api/appearance", json=VALID)
    assert put.status_code == 200
    assert put.json()["appearance"] == VALID
    assert client.get("/api/appearance").json()["appearance"] == VALID


def test_a_second_put_replaces_rather_than_duplicating(client):
    client.put("/api/appearance", json=VALID)
    changed = {**VALID, "mono": {"font": "jetbrains-mono", "weight": 500, "size": 1.05}}
    client.put("/api/appearance", json=changed)
    assert client.get("/api/appearance").json()["appearance"] == changed


@pytest.mark.parametrize("bad, why", [
    ({**VALID, "mono": {"font": "x", "weight": 950, "size": 1}}, "weight above range"),
    ({**VALID, "mono": {"font": "x", "weight": 50, "size": 1}}, "weight below range"),
    ({**VALID, "mono": {"font": "", "weight": 400, "size": 1}}, "empty font id"),
    ({**VALID, "mono": {"font": "x", "weight": 400, "size": 0}}, "size of zero"),
    ({**VALID, "mono": {"font": "x", "weight": 400}}, "missing size"),
    ({"primary": VALID["primary"]}, "missing roles"),
])
def test_shape_is_rejected(client, bad, why):
    assert client.put("/api/appearance", json=bad).status_code == 422, why


def test_a_corrupt_row_falls_back_instead_of_500ing(wired):
    """A hand-edited or half-written row must not wedge every page load — the
    client can always fall back to its own defaults, a 500 gives it nothing."""
    client, db_path = wired
    conn = db.open_write(db_path)
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 ("appearance", "{not json"))
    conn.commit()
    conn.close()

    r = client.get("/api/appearance")
    assert r.status_code == 200
    assert r.json() == {"appearance": None}


def test_delete_clears_so_the_source_style_applies(client):
    """Reset has to remove the row, not store today's values: storing them would
    look identical now and pin the install to them the moment a stylesheet moved."""
    client.put("/api/appearance", json=VALID)
    assert client.get("/api/appearance").json()["appearance"] == VALID

    assert client.delete("/api/appearance").status_code == 200
    assert client.get("/api/appearance").json() == {"appearance": None}


def test_delete_is_safe_when_nothing_is_stored(client):
    assert client.delete("/api/appearance").status_code == 200
    assert client.get("/api/appearance").json() == {"appearance": None}
