"""Odoo XML-RPC node: authenticate on /xmlrpc/2/common, call execute_kw on
/xmlrpc/2/object. proxy_factory is injectable for tests."""
import xmlrpc.client
from typing import List
from flightdeck.hub import registry, expr
from flightdeck.hub.types import NodeDef, Item, item

# Every XML-RPC call MUST carry an explicit timeout so a hung Odoo endpoint
# fails the node (error socket) instead of blocking the worker thread
# indefinitely. Single global default for MVP; per-node configurable timeout
# is deferred.
DEFAULT_TIMEOUT = 20

class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout=DEFAULT_TIMEOUT, *a, **k):
        super().__init__(*a, **k)
        self._timeout = timeout
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn

class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout=DEFAULT_TIMEOUT, *a, **k):
        super().__init__(*a, **k)
        self._timeout = timeout
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn

def _default_factory(url):
    t = _TimeoutSafeTransport() if url.lower().startswith("https") else _TimeoutTransport()
    return xmlrpc.client.ServerProxy(url, transport=t, allow_none=True)

def execute(params, inputs, ctx, proxy_factory=_default_factory) -> List[List[Item]]:
    model = None
    method = params.get("method", "search_read")
    try:
        src = (inputs[0][0] if inputs and inputs[0] else item({}))
        context = expr.build_context(src, ctx.node_outputs, ctx.vars, ctx.run_meta)
        cred = ctx.resolve_credential(params.get("credentialRef"))
        if not cred:
            raise ValueError("no odoo credential resolved for ref {!r}".format(
                params.get("credentialRef")))
        base = cred["base"].rstrip("/"); db = cred["db"]; user = cred["user"]; pwd = cred["secret"]
        model = params["model"]
        args = expr.interpolate(params.get("args", []), context)
        kwargs = expr.interpolate(params.get("kwargs", {}) or {}, context)
        common = proxy_factory(base + "/xmlrpc/2/common")
        uid = common.authenticate(db, user, pwd, {})
        obj = proxy_factory(base + "/xmlrpc/2/object")
        result = obj.execute_kw(db, uid, pwd, model, method, args, kwargs)
        return [[item({"uid": uid, "model": model, "method": method, "result": result})], []]
    except Exception as e:
        return [[], [item({"error": "{}: {}".format(type(e).__name__, e),
                           "model": model, "method": method})]]

registry.register(NodeDef("odoo.xmlrpc", "Odoo XML-RPC", ["main"], ["success", "error"],
                          run=execute,
                          params_schema={"credentialRef": "string", "model": "string",
                                         "method": "string", "args": "json", "kwargs": "json"}))
