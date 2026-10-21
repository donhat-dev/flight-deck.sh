"""POST /api/treasures — creating a treasure from the dashboard.

The two intake paths are not equivalent, and the tests say why: `source_path` lets
the server read the file itself, so the stored checksum describes what is on disk
and a later edit is detectable; `content` hashes whatever arrived.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flightdeck import db
from flightdeck.routers import treasures as treasures_router
from flightdeck.treasures import store

DOC = "# From the UI\n\nBody long enough to be a document.\n"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("TREASURES_READ_ROOTS", str(docs))

    cfg = {"db_path": str(tmp_path / "t.db"), "database_url": None,
           "projects_dir": str(tmp_path / "projects")}
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    store.init(conn)
    conn.close()

    app = FastAPI()
    app.state.cfg = cfg
    app.include_router(treasures_router.router)
    return TestClient(app), docs


def test_creates_from_pasted_content(client):
    c, _ = client
    r = c.post("/api/treasures", json={"title": "Pasted", "content": DOC})
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["title"] == "Pasted"
    assert row["version"] == 1
    # It is listed straight away, so the UI needs no second call to see it.
    listed = c.get("/api/treasures").json()
    assert any(x["id"] == row["id"] for x in listed["treasures"])


def test_creates_from_a_file_on_disk(client):
    c, docs = client
    p = docs / "on-disk.md"
    p.write_text(DOC, encoding="utf-8")

    r = c.post("/api/treasures", json={"source_path": str(p)})
    assert r.status_code == 201, r.text
    row = r.json()
    # Title and origin are DERIVED, which is the point of this path.
    assert row["origin_path"] == str(p.resolve())
    assert row["title"]


def test_a_file_created_this_way_is_refreshable(client):
    """The reason to prefer source_path: the checksum describes the file, so a
    later edit is detectable. Pasted text can never gain that property."""
    c, docs = client
    p = docs / "tracked.md"
    p.write_text(DOC, encoding="utf-8")
    ident = c.post("/api/treasures", json={"source_path": str(p)}).json()["id"]

    p.write_text(DOC + "\nEdited on disk.\n", encoding="utf-8")
    out = c.post(f"/api/treasures/{ident}/refresh").json()
    assert out["version"] == 2


def test_tags_can_be_set_at_creation(client):
    c, _ = client
    plain = c.post("/api/treasures", json={"title": "Plain", "content": DOC}).json()
    tagged = c.post("/api/treasures",
                    json={"title": "Tagged", "content": DOC + "\ndiffer\n",
                          "tags": ["  Billing ", "billing", "CRM"]}).json()

    assert tagged["tags"] == ["billing", "crm"]     # normalised by the service
    # The response must not change SHAPE just because tags were passed. Asserting
    # only the tags let an earlier version drop version/status/render_bytes from
    # the row and still pass.
    # SHAPE, not values: the two documents differ, so their sizes differ too.
    # Comparing values here was the wrong assertion; what must hold is that
    # passing tags does not remove fields from the response.
    assert set(plain) - {"tags"} <= set(tagged), "tagging lost fields from the row"
    for key in ("version", "status", "render_bytes", "slug", "artifact_path"):
        assert tagged.get(key) is not None, f"{key} missing when tags were passed"


# --- fail-closed paths ------------------------------------------------------
def test_rejects_neither_content_nor_path(client):
    c, _ = client
    r = c.post("/api/treasures", json={"title": "Empty"})
    assert r.status_code == 400
    assert "exactly one" in r.json()["detail"]


def test_rejects_both_content_and_path(client):
    c, docs = client
    p = docs / "both.md"
    p.write_text(DOC, encoding="utf-8")
    r = c.post("/api/treasures", json={"content": DOC, "source_path": str(p)})
    assert r.status_code == 400


def test_rejects_content_without_a_title(client):
    """With a path the title is derived; with text there is nothing to derive."""
    c, _ = client
    r = c.post("/api/treasures", json={"content": DOC})
    assert r.status_code == 400
    assert "title" in r.json()["detail"]


def test_rejects_a_path_outside_the_read_roots(client):
    """The server reads this path, so an unbounded path would be a file-disclosure
    hole: anything readable could be rendered into an artifact and served."""
    c, _ = client
    r = c.post("/api/treasures", json={"source_path": "/etc/hosts"})
    assert r.status_code == 400
    assert c.get("/api/treasures").json()["count"] == 0      # nothing written


def test_rejects_an_unknown_component_and_writes_nothing(client):
    c, _ = client
    bad = '<div data-component="nope">\n\nhi\n\n</div>\n'
    r = c.post("/api/treasures", json={"title": "Bad", "content": bad})
    assert r.status_code == 400
    assert c.get("/api/treasures").json()["count"] == 0
