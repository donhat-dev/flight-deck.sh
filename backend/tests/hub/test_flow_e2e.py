from flightdeck.hub import engine
from flightdeck.hub.nodes import load, http

def test_end_to_end_start_http_condition(monkeypatch):
    load.load_all()
    # fake HTTP transport: /login -> token, and status echo
    def transport(method, url, headers, body, timeout):
        if url.endswith("/login"):
            return 200, {}, {"token": "T"}
        return 200, {}, {"auth": headers.get("Authorization")}
    monkeypatch.setattr(http, "_urllib_transport", transport)

    flow = {
        "id": "e2e", "name": "login-then-call",
        "nodes": [
            {"id": "s", "type": "start", "label": "Start", "params": {"seed": {}}},
            {"id": "login", "type": "http", "label": "Login",
             "params": {"method": "POST", "url": "http://svc/login"}},
            {"id": "call", "type": "http", "label": "Call",
             "params": {"method": "GET", "url": "http://svc/me",
                        "headers": {"Authorization": "Bearer {{ $node.Login.json.json.token }}"}}},
        ],
        "connections": [
            {"from": ["s", 0], "to": ["login", 0]},
            {"from": ["login", 0], "to": ["call", 0]},   # success socket -> call
        ],
    }
    res = engine.run_flow(flow)
    assert res["status"] == "ok"
    assert res["nodes"]["call"]["status"] == "ok"
    assert res["nodes"]["call"]["outputsPerSocket"][0][0]["json"]["json"]["auth"] == "Bearer T"
