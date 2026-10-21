"""The two wires to Odoo, behind one seam — and the reason there are two.

**Reads go over XML-RPC, anything that can write goes over `/jsonrpc`.** This is not a
preference. Odoo 12 serves XML-RPC from `odoo/addons/base/controllers/rpc.py`:

    return dumps((result,), methodresponse=1, allow_none=False)

`dispatch_rpc` has already run and committed by the time that line serialises. So a
method returning `None` — every `action_*` and `button_*` written the ordinary Odoo way
— raises `TypeError: cannot marshal None` **after the database changed**. The caller
sees a failure over a write that succeeded. That has happened in this workspace.

`/jsonrpc` has no marshalling layer to break, but it has its own trap, in
`odoo/http.py`:

    if error is not None:  response['error'] = error
    if result is not None: response['result'] = result

A method returning `None` produces `{"jsonrpc": "2.0", "id": 1}` — no `result`, and no
`error`. Reading `resp["result"]` raises `KeyError` on exactly the case the transport
switch exists to fix, so that shape is parsed explicitly here as success-with-null.

Both wires run through `execute()`, so collapsing onto one transport later is a change
to one default rather than a rewrite of the tools.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xmlrpc.client

# Below the 120 s ceiling the MCP wrapper puts on a call, so a slow instance comes back
# as a structured transport error rather than as the wrapper killing the process.
DEFAULT_TIMEOUT = 60

# Odoo reports a bad credential as an exception on this class; everything else on
# AccessError is the record rules and access rights doing their job.
_AUTH_EXCEPTIONS = ("AccessDenied",)
_ACL_EXCEPTIONS = ("AccessError",)

_EXC_LINE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Denied|Exception|Warning)):", re.M)


def scrub(value, secret: str):
    """Replace the password wherever it appears in a payload.

    Cheap insurance rather than a claim about Odoo: no error path here is known to echo
    the credential, and this makes that true by construction instead of by inspection.
    """
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "***")
    if isinstance(value, dict):
        return {k: scrub(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secret) for v in value]
    return value


class _TimeoutTransport(xmlrpc.client.Transport):
    """`xmlrpc.client` has no timeout argument; the connection it builds does."""

    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


def _transport_error(inst, exc, extra=None) -> dict:
    out = {"error": f"cannot reach instance {inst.name!r}", "layer": "transport",
           "instance": inst.name, "base_url": inst.base_url,
           "detail": f"{type(exc).__name__}: {exc}",
           "hint": "the instance may be down, or unreachable from this machine"}
    out.update(extra or {})
    return out


def _classify(exc_name: str, message: str, ctx: dict, debug: str = "") -> dict:
    """One Odoo-side failure, sorted into the ACL layer or the ORM layer."""
    short = exc_name.rsplit(".", 1)[-1]
    if short in _AUTH_EXCEPTIONS:
        layer, headline = "auth", f"Odoo rejected the credential on {ctx['instance']!r}"
    elif short in _ACL_EXCEPTIONS:
        layer = "odoo_acl"
        headline = f"Odoo refused access to {ctx.get('model')!r}"
    else:
        layer = "orm"
        headline = f"Odoo refused the operation on {ctx.get('model')!r}"
    out = {"error": headline, "layer": layer, **ctx,
           "odoo_exception": exc_name or "unknown",
           "odoo_message": (message or "").strip()}
    if debug:
        # Enough to find the file, not enough to flood the answer.
        tail = [ln for ln in debug.strip().splitlines() if ln.strip()][-3:]
        if tail:
            out["traceback_tail"] = tail
    return out


def parse_jsonrpc(resp: dict, ctx: dict) -> dict:
    """The `/jsonrpc` envelope, including the shape that has no `result` key.

    `{"jsonrpc": "2.0", "id": 1}` means the method returned `None` and everything went
    fine. It is the exact case XML-RPC turns into a `TypeError` after committing.
    """
    if "error" in resp:
        err = resp["error"] or {}
        data = err.get("data") or {}
        return _classify(data.get("name") or "", data.get("message") or
                         err.get("message") or "", ctx, data.get("debug") or "")
    return {"ok": True, "result": resp.get("result")}


def _fault_to_error(fault: xmlrpc.client.Fault, ctx: dict) -> dict:
    """XML-RPC gives one string where JSON-RPC gives a structure; dig the class name
    out of the traceback tail so both wires classify the same way."""
    text = fault.faultString or ""
    names = _EXC_LINE.findall(text)
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return _classify(names[-1] if names else "", lines[-1] if lines else text,
                     ctx, "\n".join(lines))


def authenticate(inst, timeout=DEFAULT_TIMEOUT) -> tuple[int | None, dict | None]:
    """uid for this instance, or an error. Every call authenticates again.

    No uid cache: the password travels with every `execute_kw` regardless, so a cache
    would save one localhost round trip in exchange for a state file that can hand a
    rebuilt database a uid from the database before it.
    """
    password, err = inst.password()
    if err:
        return None, err
    common = xmlrpc.client.ServerProxy(
        inst.base_url + "/xmlrpc/2/common", transport=_TimeoutTransport(timeout),
        allow_none=True)
    try:
        uid = common.authenticate(inst.db, inst.login, password, {})
    except xmlrpc.client.Fault as f:
        return None, scrub(_fault_to_error(f, {"instance": inst.name, "db": inst.db}),
                           password)
    except (OSError, urllib.error.URLError, xmlrpc.client.ProtocolError) as e:
        return None, scrub(_transport_error(inst, e), password)
    if not uid:
        # Odoo answers a wrong credential with uid=False rather than an exception.
        return None, {"error": f"authentication failed on {inst.name!r}",
                      "layer": "auth", "instance": inst.name, "db": inst.db,
                      "login": inst.login, "detail": "Odoo returned uid=False",
                      "hint": f"check {inst.password_env or 'the password'} "
                              f"and the database name"}
    return int(uid), None


def version(inst, timeout=DEFAULT_TIMEOUT) -> tuple[dict | None, dict | None]:
    common = xmlrpc.client.ServerProxy(
        inst.base_url + "/xmlrpc/2/common", transport=_TimeoutTransport(timeout),
        allow_none=True)
    try:
        return common.version(), None
    except (OSError, urllib.error.URLError, xmlrpc.client.ProtocolError) as e:
        return None, _transport_error(inst, e)
    except xmlrpc.client.Fault as f:
        return None, _fault_to_error(f, {"instance": inst.name})


def _execute_xmlrpc(inst, uid, password, model, method, args, kwargs, ctx, timeout):
    proxy = xmlrpc.client.ServerProxy(
        inst.base_url + "/xmlrpc/2/object", transport=_TimeoutTransport(timeout),
        allow_none=True)
    try:
        result = proxy.execute_kw(inst.db, uid, password, model, method, args, kwargs)
    except xmlrpc.client.Fault as f:
        return _fault_to_error(f, ctx)
    except (OSError, urllib.error.URLError, xmlrpc.client.ProtocolError) as e:
        return _transport_error(inst, e, ctx)
    return {"ok": True, "result": result}


def _execute_jsonrpc(inst, uid, password, model, method, args, kwargs, ctx, timeout):
    body = json.dumps({
        "jsonrpc": "2.0", "method": "call", "id": 1,
        "params": {"service": "object", "method": "execute_kw",
                   "args": [inst.db, uid, password, model, method, args, kwargs]},
    }).encode("utf-8")
    req = urllib.request.Request(
        inst.base_url + "/jsonrpc", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            payload = json.loads(fh.read().decode("utf-8"))
    except (OSError, urllib.error.URLError) as e:
        return _transport_error(inst, e, ctx)
    except ValueError as e:
        return {"error": f"instance {inst.name!r} answered with something that is not "
                         f"JSON", "layer": "transport", "instance": inst.name,
                "detail": f"{type(e).__name__}: {e}"}
    return parse_jsonrpc(payload, ctx)


def execute(inst, uid, model, method, args=None, kwargs=None, *,
            transport="xmlrpc", timeout=DEFAULT_TIMEOUT) -> dict:
    """One `execute_kw`, over whichever wire the caller named.

    `transport="xmlrpc"` is for reads only. Anything that can write passes
    `transport="jsonrpc"`, with no exception for a method known to return a value.
    """
    password, err = inst.password()
    if err:
        return err
    ctx = {"instance": inst.name, "model": model, "method": method, "uid": uid}
    runner = _execute_jsonrpc if transport == "jsonrpc" else _execute_xmlrpc
    out = runner(inst, uid, password, model, method,
                 list(args or []), dict(kwargs or {}), ctx, timeout)
    return scrub(out, password)
