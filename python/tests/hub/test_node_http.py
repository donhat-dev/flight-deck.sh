from flightdeck.hub.nodes import http
from flightdeck.hub.engine import RunContext
from flightdeck.hub.types import item

def fake_transport(method, url, headers, body, timeout):
    # echo a deterministic response keyed by url
    if url.endswith("/boom"):
        return 500, {"h": "1"}, {"error": "kaboom"}
    return 200, {"h": "1"}, {"echo": {"method": method, "url": url}}

def test_http_success_goes_to_socket_0():
    ctx = RunContext()
    out = http.execute({"method": "GET", "url": "http://x/ok"}, [[item({})]], ctx,
                       transport=fake_transport)
    assert out[0] and not out[1]
    assert out[0][0]["json"]["status"] == 200
    assert out[0][0]["json"]["json"]["echo"]["url"] == "http://x/ok"

def test_http_error_goes_to_socket_1():
    ctx = RunContext()
    out = http.execute({"method": "GET", "url": "http://x/boom"}, [[item({})]], ctx,
                       transport=fake_transport)
    assert not out[0] and out[1]
    assert out[1][0]["json"]["status"] == 500

def test_http_interpolates_url_from_prior_node():
    ctx = RunContext(); ctx.node_outputs = {"Login": {"json": {"id": 9}}}
    out = http.execute({"method": "GET", "url": "http://x/p/{{ $node.Login.json.id }}"},
                       [[item({})]], ctx, transport=fake_transport)
    assert out[0][0]["json"]["json"]["echo"]["url"] == "http://x/p/9"

def test_http_transport_exception_routes_to_error_socket():
    def raising_transport(method, url, headers, body, timeout):
        raise ConnectionError("refused")
    ctx = RunContext()
    out = http.execute({"method": "GET", "url": "http://x/down"}, [[item({})]], ctx,
                       transport=raising_transport)
    assert out[0] == []
    assert len(out[1]) == 1
    assert "refused" in out[1][0]["json"]["error"]
    assert out[1][0]["json"]["status"] is None

def test_http_bearer_credential_injected():
    captured = {}
    def echoing_transport(method, url, headers, body, timeout):
        captured["headers"] = headers
        return 200, {}, {"ok": True}
    ctx = RunContext(resolve_credential=lambda ref: {"kind": "bearer", "token": "T"})
    out = http.execute({"method": "GET", "url": "http://x/ok", "credentialRef": "cred1"},
                       [[item({})]], ctx, transport=echoing_transport)
    assert captured["headers"]["Authorization"] == "Bearer T"
    assert out[0] and not out[1]
