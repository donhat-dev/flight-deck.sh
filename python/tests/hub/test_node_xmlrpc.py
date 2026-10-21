import xmlrpc.client

from flightdeck.hub.nodes import odoo_xmlrpc as x
from flightdeck.hub.engine import RunContext
from flightdeck.hub.types import item

class FakeCommon:
    def authenticate(self, db, user, pwd, opts): return 7
class FakeObject:
    def execute_kw(self, db, uid, pwd, model, method, args, kwargs=None):
        return [{"id": 1, "name": "Deal A"}]
def fake_factory(url):
    return FakeCommon() if url.endswith("/common") else FakeObject()

def test_xmlrpc_search_read_success():
    ctx = RunContext(resolve_credential=lambda ref: {
        "kind": "odoo", "base": "http://x", "db": "nakivo", "user": "a", "secret": "p"})
    out = x.execute({"credentialRef": "c1", "model": "crm.lead",
                     "method": "search_read", "args": [[]], "kwargs": {"limit": 1}},
                    [[item({})]], ctx, proxy_factory=fake_factory)
    assert out[0] and not out[1]
    assert out[0][0]["json"]["result"][0]["name"] == "Deal A"

def test_xmlrpc_error_to_socket_1():
    def boom_factory(url):
        class C:
            def authenticate(self, *a): return 7
        class O:
            def execute_kw(self, *a, **k): raise RuntimeError("bad domain")
        return C() if url.endswith("/common") else O()
    ctx = RunContext(resolve_credential=lambda ref: {
        "kind": "odoo", "base": "http://x", "db": "d", "user": "a", "secret": "p"})
    out = x.execute({"credentialRef": "c1", "model": "crm.lead", "method": "search_read",
                     "args": [[]]}, [[item({})]], ctx, proxy_factory=boom_factory)
    assert not out[0] and out[1]
    assert "bad domain" in out[1][0]["json"]["error"]

def test_default_factory_sets_timeout():
    px = x._default_factory("http://x/xmlrpc/2/common")
    transport = px.__dict__["_ServerProxy__transport"]
    assert getattr(transport, "_timeout") == x.DEFAULT_TIMEOUT

def test_default_factory_https_uses_safe_transport():
    px = x._default_factory("https://x/xmlrpc/2/common")
    transport = px.__dict__["_ServerProxy__transport"]
    assert isinstance(transport, xmlrpc.client.SafeTransport)
    assert getattr(transport, "_timeout") == x.DEFAULT_TIMEOUT

def test_xmlrpc_missing_model_routes_to_error():
    ctx = RunContext(resolve_credential=lambda ref: {
        "kind": "odoo", "base": "http://x", "db": "d", "user": "a", "secret": "p"})
    out = x.execute({"credentialRef": "c1", "method": "search_read", "args": [[]]},
                    [[item({})]], ctx, proxy_factory=fake_factory)
    assert out[0] == []
    assert out[1] and out[1][0]["json"]["error"]

def test_xmlrpc_missing_credential_routes_to_error():
    ctx = RunContext(resolve_credential=lambda ref: None)
    out = x.execute({"credentialRef": "missing", "model": "crm.lead",
                     "method": "search_read", "args": [[]]},
                    [[item({})]], ctx, proxy_factory=fake_factory)
    assert out[0] == []
    assert out[1] and out[1][0]["json"]["error"]
