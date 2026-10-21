import os
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_AUDIT_FLOWS_DIR", str(tmp_path))
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", "config.toml")
    from flightdeck.server import create_app
    return TestClient(create_app())


def test_node_types_lists_registered(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    types = {n["type"] for n in c.get("/api/hub/node-types").json()}
    assert {"start", "http", "odoo.xmlrpc", "set", "condition"} <= types


def test_flow_crud_and_run(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    flow = {"name": "just-start",
            "nodes": [{"id": "s", "type": "start", "label": "Start",
                       "params": {"seed": {"hello": 1}}}],
            "connections": []}
    saved = c.post("/api/hub/flows", json=flow).json()
    assert saved["id"]
    run = c.post("/api/hub/run", json={"flowId": saved["id"]}).json()
    assert run["status"] == "ok"
    assert run["nodes"]["s"]["outputsPerSocket"][0][0]["json"] == {"hello": 1}


def test_flow_get_missing_id_is_404(tmp_path, monkeypatch):
    # Valid slug (passes store._SAFE_ID) but no file was ever saved for it ->
    # store.load_flow returns None -> route must map that to 404, not 500.
    c = _client(tmp_path, monkeypatch)
    resp = c.get("/api/hub/flows/does-not-exist")
    assert resp.status_code == 404


def test_flow_get_invalid_id_is_400(tmp_path, monkeypatch):
    # Fails store._SAFE_ID (contains a dot) -> store.load_flow raises ValueError
    # (path-traversal guard) -> route must map that to 400, not an uncaught 500.
    # Single path segment (no "/") so the URL itself isn't dot-segment-normalized
    # away before it reaches our route.
    c = _client(tmp_path, monkeypatch)
    resp = c.get("/api/hub/flows/bad.id")
    assert resp.status_code == 400


def test_save_credential_malformed_400(tmp_path, monkeypatch):
    # A body missing name/kind/data must be a 400, not an uncaught KeyError -> 500.
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/hub/credentials", json={"foo": "bar"}).status_code == 400


def test_run_flow_without_nodes_400(tmp_path, monkeypatch):
    # A flow body missing nodes/connections must be rejected at save AND at run,
    # rather than 500ing deeper in engine.run_flow.
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/hub/flows", json={"name": "no-nodes"}).status_code == 400
    assert c.post("/api/hub/run", json={"flow": {"name": "x"}}).status_code == 400


def test_malformed_node_or_connection_400(tmp_path, monkeypatch):
    # Element-level shape: a node without id/type, or a connection without
    # from/to, must be 400 at save AND at run (not an uncaught 500 in run_flow).
    c = _client(tmp_path, monkeypatch)
    bad_node = {"name": "x", "nodes": [{"type": "start"}], "connections": []}
    assert c.post("/api/hub/flows", json=bad_node).status_code == 400
    assert c.post("/api/hub/run", json={"flow": bad_node}).status_code == 400
    bad_conn = {"name": "x",
                "nodes": [{"id": "s", "type": "start"}],
                "connections": [{"from": ["s"]}]}
    assert c.post("/api/hub/flows", json=bad_conn).status_code == 400
    assert c.post("/api/hub/run", json={"flow": bad_conn}).status_code == 400
