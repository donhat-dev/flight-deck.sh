import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flightdeck import db
from flightdeck.routers import treasures as treasures_router
from flightdeck.treasures import service, store


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    cfg = {"db_path": str(tmp_path / "t.db"), "database_url": None}
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    store.init(conn)
    # two artifacts: one EN draft, one VN draft
    service.wrap(conn, title="Helpdesk report", content="# Helpdesk\n\nBody.\n",
                 origin_kind="claude_session", origin_id="sess-en")
    service.wrap(conn, title="Báo cáo", content="# Báo cáo\n\nNội dung.\n",
                 language="vi", origin_kind="claude_session", origin_id="sess-vi")
    app = FastAPI()
    app.state.cfg = cfg
    app.include_router(treasures_router.router)
    return TestClient(app)


def test_list_returns_rows_newest_first(client):
    r = client.get("/api/treasures")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert {t["language"] for t in body["treasures"]} == {"en", "vi"}
    # every row carries the fields the UI renders
    row = body["treasures"][0]
    for key in ("id", "title", "slug", "kind", "language", "status", "version",
                "render_bytes", "origin_id", "updated_at"):
        assert key in row


def test_list_filters(client):
    assert client.get("/api/treasures?language=vi").json()["count"] == 1
    assert client.get("/api/treasures?status=published").json()["count"] == 0
    assert client.get("/api/treasures?query=helpdesk").json()["count"] == 1
    assert client.get("/api/treasures?origin_id=sess-vi").json()["count"] == 1


def test_get_one_with_and_without_source(client):
    ident = client.get("/api/treasures?language=vi").json()["treasures"][0]["id"]
    plain = client.get(f"/api/treasures/{ident}").json()
    assert plain["title"] == "Báo cáo"
    assert "source" not in plain
    full = client.get(f"/api/treasures/{ident}?include_source=true").json()
    assert full["source"].startswith("# Báo cáo")
    assert client.get("/api/treasures/nope").status_code == 404


def test_raw_serves_the_artifact_html(client):
    ident = client.get("/api/treasures?language=vi").json()["treasures"][0]["id"]
    r = client.get(f"/api/treasures/{ident}/raw")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.lstrip().startswith("<!doctype html>")
    assert "data:font/woff2;base64," in r.text     # self-contained
    assert client.get("/api/treasures/nope/raw").status_code == 404


def test_put_source_creates_a_new_version(client):
    ident = client.get("/api/treasures?language=vi").json()["treasures"][0]["id"]
    before = client.get(f"/api/treasures/{ident}").json()
    assert before["version"] == 1

    r = client.put(f"/api/treasures/{ident}/source",
                   json={"content": "# Báo cáo v2\n\nĐã sửa nội dung.\n"})
    assert r.status_code == 200
    after = r.json()
    assert after["version"] == 2
    assert after["id"] == ident
    assert after["source_checksum"] != before["source_checksum"]

    # the new source is what /raw and include_source now serve
    assert "Báo cáo v2" in client.get(f"/api/treasures/{ident}/raw").text
    assert client.get(f"/api/treasures/{ident}?include_source=true"
                      ).json()["source"].startswith("# Báo cáo v2")
    assert client.put("/api/treasures/nope/source",
                      json={"content": "x" * 10}).status_code == 404
