"""HTTP Request node. Server-side execution with a hard timeout so a hung target
fails the node (error socket), never a worker thread."""
import json as _json
import urllib.error
import urllib.request
from typing import List
from flightdeck.hub import registry, expr
from flightdeck.hub.types import NodeDef, Item, item

DEFAULT_TIMEOUT = 20

def _urllib_transport(method, url, headers, body, timeout):
    data = None
    if body is not None:
        data = (body if isinstance(body, bytes)
                else _json.dumps(body).encode("utf-8"))
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status, resp_headers = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status, resp_headers = e.code, dict(e.headers or {})
    try:
        parsed = _json.loads(raw)
    except ValueError:
        parsed = None
    return status, resp_headers, parsed if parsed is not None else raw

def execute(params, inputs, ctx, transport=None) -> List[List[Item]]:
    # Resolved at call-time (not bound at def-time) so a later monkeypatch of
    # module-level `_urllib_transport` (e.g. in tests) takes effect even when
    # callers don't pass `transport` explicitly.
    transport = transport or _urllib_transport
    src = (inputs[0][0] if inputs and inputs[0] else item({}))
    context = expr.build_context(src, ctx.node_outputs, ctx.vars, ctx.run_meta)
    method = (params.get("method") or "GET").upper()
    url = expr.interpolate(params.get("url", ""), context)
    headers = expr.interpolate(params.get("headers", {}) or {}, context)
    body = expr.interpolate(params.get("body"), context) if params.get("body") is not None else None
    # credential injection (bearer only, for now) if a ref is set
    cred = ctx.resolve_credential(params.get("credentialRef"))
    if cred and cred.get("kind") == "bearer":
        headers = dict(headers); headers["Authorization"] = "Bearer " + cred["token"]
    timeout = params.get("timeout") or DEFAULT_TIMEOUT
    import time as _t; t0 = _t.time()
    try:
        status, resp_headers, parsed = transport(method, url, headers, body, timeout)
    except (OSError, urllib.error.URLError) as e:
        error_item = item({"status": None, "error": str(e),
                           "timeMs": round((_t.time() - t0) * 1000, 2)})
        return [[], [error_item]]
    out_item = item({"status": status, "headers": resp_headers, "json": parsed,
                     "timeMs": round((_t.time() - t0) * 1000, 2)})
    success_when = params.get("successMaxStatus", 399)
    if status <= success_when:
        return [[out_item], []]
    return [[], [out_item]]

registry.register(NodeDef("http", "HTTP Request", ["main"], ["success", "error"],
                          run=execute,
                          params_schema={"method": "string", "url": "string",
                                         "headers": "json", "body": "json",
                                         "credentialRef": "string"}))
