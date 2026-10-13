"""GET /api/treasures/{id}/versions and /raw?version=N — the history surface.

The history is DERIVED from the `v<N>/` directories on disk, not stored, so these
tests are about that derivation being honest: it must report what is actually
retained (including a version whose render was removed), must not invent a
creation record it does not have, and must refuse to read outside the filestore
when the version number comes from the request.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flightdeck import db
from flightdeck.routers import treasures as treasures_router
from flightdeck.treasures import store

DOC = "# Versioned\n\nA body long enough to render as a document.\n"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("TREASURES_READ_ROOTS", str(tmp_path / "docs"))

    cfg = {"db_path": str(tmp_path / "t.db"), "database_url": None,
           "projects_dir": str(tmp_path / "projects")}
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    store.init(conn)
    conn.close()

    app = FastAPI()
    app.state.cfg = cfg
    app.include_router(treasures_router.router)
    return TestClient(app)


def make(c, n_saves=0):
    """A treasure at version 1+n_saves, each save producing a new v<N>/ dir."""
    row = c.post("/api/treasures", json={"title": "Versioned", "content": DOC}).json()
    for i in range(n_saves):
        # Each edit is a DIFFERENT length, so a test that compares per-version
        # sizes is actually comparing distinct documents. `f"Edit {i}"` alone is
        # the same length every time and made that comparison vacuous.
        r = c.put(f"/api/treasures/{row['id']}/source",
                  json={"content": DOC + f"\nEdit {i}. " + "x" * (10 * (i + 1)) + "\n"})
        assert r.status_code == 200, r.text
    return row


def test_a_new_treasure_has_exactly_one_version(client):
    row = make(client)
    body = client.get(f"/api/treasures/{row['id']}/versions").json()
    assert body["count"] == 1
    assert body["versions"][0]["version"] == 1
    assert body["versions"][0]["is_current"] is True


def test_every_save_is_retained_and_listed_newest_first(client):
    row = make(client, n_saves=3)
    body = client.get(f"/api/treasures/{row['id']}/versions").json()

    assert [v["version"] for v in body["versions"]] == [4, 3, 2, 1]
    # Exactly one row may claim to be current, and it must be the highest.
    current = [v["version"] for v in body["versions"] if v["is_current"]]
    assert current == [4]


def test_each_version_reports_its_own_real_byte_sizes(client):
    row = make(client, n_saves=2)
    versions = client.get(f"/api/treasures/{row['id']}/versions").json()["versions"]

    # Each save appended text, so sizes must differ per version — a single size
    # repeated down the list would mean the current row was reported three times.
    sizes = [v["source_bytes"] for v in versions]
    assert len(set(sizes)) == 3
    assert all(v["render_bytes"] > 0 for v in versions)
    assert all(v["has_artifact"] is True for v in versions)


def test_it_does_not_claim_a_creation_time_it_cannot_know(client):
    row = make(client)
    v = client.get(f"/api/treasures/{row['id']}/versions").json()["versions"][0]
    # The field is the artifact file's mtime. Calling it `created_at` would assert
    # a recorded creation event that nothing in this system stores.
    assert "created_at" not in v
    assert v["written_at"].endswith("+00:00")


def test_a_version_whose_render_was_removed_is_still_reported(client):
    row = make(client, n_saves=1)
    (Path(row["dir_path"]) / "v1" / "artifact.html").unlink()

    versions = client.get(f"/api/treasures/{row['id']}/versions").json()["versions"]
    v1 = next(v for v in versions if v["version"] == 1)
    # Hiding it would under-report what is on disk; the source is still readable.
    assert v1["has_artifact"] is False
    assert v1["render_bytes"] is None
    assert v1["source_bytes"] > 0


def test_raw_serves_a_historical_version(client):
    row = make(client, n_saves=1)
    current = client.get(f"/api/treasures/{row['id']}/raw")
    old = client.get(f"/api/treasures/{row['id']}/raw", params={"version": 1})

    assert old.status_code == 200
    assert "Edit 0." not in old.text  # v1 predates the edit
    assert "Edit 0." in current.text
    assert old.headers["cache-control"] == "no-store"


def test_raw_refuses_a_version_that_does_not_exist(client):
    row = make(client)
    r = client.get(f"/api/treasures/{row['id']}/raw", params={"version": 99})
    assert r.status_code == 404


def test_raw_cannot_be_walked_out_of_the_filestore(client):
    row = make(client)
    # `version` is request-supplied. FastAPI's int coercion rejects a traversal
    # string outright; a negative number stays inside the type but must not
    # resolve to a readable file either.
    assert client.get(f"/api/treasures/{row['id']}/raw",
                      params={"version": "../../../../etc"}).status_code == 422
    assert client.get(f"/api/treasures/{row['id']}/raw",
                      params={"version": -1}).status_code == 404


def test_versions_404s_for_an_unknown_treasure(client):
    assert client.get("/api/treasures/nope-does-not-exist/versions").status_code == 404
