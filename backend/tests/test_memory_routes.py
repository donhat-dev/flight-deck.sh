"""The HTTP surface over the memory domain.

The domain itself is already covered by seven test files; what is untested until here
is the translation layer — a `{"error": ...}` dict becoming a status code a client can
branch on, and the `cwd` fallback that decides which store a bare request reads.

The store is built under `tmp_path` and reached by pointing `CLAUDE_CONFIG_DIR` at it,
which is the same seam `paths.py` reads. No test here touches the real store.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flightdeck.memory import paths
from flightdeck.routers import memory as memory_router

PROJECT = "/tmp/a-project"


def _write_store(root):
    """A store with one finding of each kind the tab renders."""
    store = root / "projects" / paths.sanitize(PROJECT) / "memory"
    store.mkdir(parents=True)
    (store / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"Where the worktree is mounted from\"\n"
        "type: project\n---\n\nBody naming a worktree and linking to [[beta]].\n",
        encoding="utf-8")
    (store / "beta.md").write_text(
        "---\nname: beta\ndescription: \"A summary wholly unalike the first one\"\n"
        "type: project\n---\n\nPlain body, and a link to [[gamma-does-not-exist]].\n",
        encoding="utf-8")
    # On disk but absent from the index below: the "not in index" finding.
    (store / "orphan.md").write_text(
        "---\nname: orphan\ndescription: \"Never listed in the index at all\"\n"
        "type: feedback\n---\n\nNothing points here and it points nowhere.\n",
        encoding="utf-8")
    (store / "MEMORY.md").write_text(
        "- [Alpha](alpha.md) — hook\n- [Beta](beta.md) — hook\n", encoding="utf-8")
    return store


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FLIGHTDECK_WORKSPACE", raising=False)
    _write_store(tmp_path)
    app = FastAPI()
    app.include_router(memory_router.router)
    return TestClient(app)


def test_lint_reports_every_kind_the_tab_renders(client):
    body = client.get(f"/api/memory/lint?cwd={PROJECT}").json()
    assert body["total_memories"] == 3
    assert body["counts"]["not_in_index"] == 1
    assert body["counts"]["broken_link"] == 1
    assert body["counts"]["not_linked"] == 1
    kinds = {f["kind"] for f in body["findings"]}
    assert {"not_in_index", "broken_link", "not_linked"} <= kinds
    # `with_usage` is what makes "never read" renderable, and it is on by default.
    assert body["never_read"] == ["alpha.md", "beta.md", "orphan.md"]


def test_lint_can_skip_the_transcript_scan(client):
    body = client.get(f"/api/memory/lint?cwd={PROJECT}&with_usage=false").json()
    assert "usage" not in body


def test_a_missing_store_is_a_404_that_says_where_it_looked(client):
    r = client.get("/api/memory/lint?cwd=/tmp/no-such-project")
    assert r.status_code == 404
    assert "no memory store at" in r.json()["detail"]


def test_search_finds_the_body_and_carries_the_neighbours(client):
    body = client.get(f"/api/memory/search?cwd={PROJECT}&query=worktree").json()
    assert body["in_memories"] == 1
    hit = body["results"][0]
    assert hit["name"] == "alpha"
    assert "worktree" in hit["snippet"]
    assert [n["name"] for n in hit["neighbours"]] == ["beta"]


def test_search_limit_and_empty_query(client):
    many = client.get(f"/api/memory/search?cwd={PROJECT}&query=a&limit=1").json()
    assert len(many["results"]) == 1
    r = client.get(f"/api/memory/search?cwd={PROJECT}&query=%20%20")
    assert r.status_code == 400
    assert "empty query" in r.json()["detail"]
    # A query is required, not defaulted — an accidental bare call must not scan.
    assert client.get(f"/api/memory/search?cwd={PROJECT}").status_code == 422


def test_graph_keeps_the_broken_edge_rather_than_hiding_it(client):
    body = client.get(f"/api/memory/graph?cwd={PROJECT}").json()
    assert body["missing_targets"] == ["gamma-does-not-exist"]
    broken = [e for e in body["edges"] if e["broken"]]
    assert broken == [{"source": "beta", "target": "gamma-does-not-exist",
                       "broken": True}]
    # Fewest links first, so the unconnected memory is the first row.
    assert body["nodes"][0]["name"] == "orphan"
    assert {"name", "type", "in", "out", "description", "last_updated"} <= set(
        body["nodes"][0])
    assert set(body["groups"]) == {"project", "feedback"}


def test_history_refuses_a_name_and_a_store_it_cannot_serve(client):
    unknown = client.get(f"/api/memory/history?cwd={PROJECT}&name=nope")
    assert unknown.status_code == 404
    assert "no memory named" in unknown.json()["detail"]
    # A real name in a store with no sidecar repository: a different problem, and
    # a different code, because the fix is to create the repository.
    uninitialised = client.get(f"/api/memory/history?cwd={PROJECT}&name=alpha")
    assert uninitialised.status_code == 409
    assert "not under version control" in uninitialised.json()["detail"]


def test_history_never_offers_the_write(client):
    """`init=true` creates a git repository. It is a query param the router ignores."""
    r = client.get(f"/api/memory/history?cwd={PROJECT}&name=alpha&init=true")
    assert r.status_code == 409          # still the read path, still refused
    assert not (paths.memory_dir(PROJECT).parent / "memory.git").exists()


def test_cwd_falls_back_to_the_workspace_the_app_already_pins(monkeypatch, client):
    """uvicorn runs with its working directory at `backend/`, which has no store.

    Without this fallback every default request 404s, so the tab could never open
    without the caller knowing the project path.
    """
    assert client.get("/api/memory/lint").status_code == 404
    monkeypatch.setenv("FLIGHTDECK_WORKSPACE", PROJECT)
    assert client.get("/api/memory/lint").json()["total_memories"] == 3


def test_an_explicit_cwd_beats_the_environment(monkeypatch, client):
    monkeypatch.setenv("FLIGHTDECK_WORKSPACE", "/tmp/no-such-project")
    assert client.get(f"/api/memory/lint?cwd={PROJECT}").json()["total_memories"] == 3


def test_reading_the_store_leaves_it_exactly_as_it_was(client, tmp_path):
    """The domain's read-only claim, re-checked at the surface that exposes it."""
    store = paths.memory_dir(PROJECT)
    before = {p.name: p.read_bytes() for p in sorted(store.iterdir())}
    for path in (f"/api/memory/lint?cwd={PROJECT}",
                 f"/api/memory/search?cwd={PROJECT}&query=a",
                 f"/api/memory/graph?cwd={PROJECT}",
                 f"/api/memory/history?cwd={PROJECT}&name=alpha"):
        client.get(path)
    assert {p.name: p.read_bytes() for p in sorted(store.iterdir())} == before
