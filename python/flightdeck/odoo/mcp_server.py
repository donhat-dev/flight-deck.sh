"""The `odoo_*` agent surface: a controlled read/write corridor into live Odoo servers.

Seven tools. Four read over XML-RPC, three write over `/jsonrpc`, and the split is a
hard requirement rather than a style — see `transport.py` for the line of Odoo source
that makes it one.

What this domain is *for*, against the two things that already exist:

- `odoo_graph` answers "how is the code put together" from the repository. It does not
  know which server actually has a module installed.
- `Projects/scripts/` answers "is the local stack up, how many rows" with fixed
  read-only probes against one stack.
- These tools answer "what does this record look like right now, on that named server,
  and change it" — live, on several servers, behind an allowlist.

Every tool that touches Odoo takes `instance=`. There is no default instance and no way
to add one. Two of the declared servers hold records with the same ids, and a mis-aimed
link has already opened an unrelated real record here with no error at all.

`odoo12-local` is an anonymised restore of production with crons and mail switched off
and no backup. It answers questions about *the restore*, never about production, and its
allowlist stays empty for good.
"""
from __future__ import annotations

from flightdeck.odoo import config, guard, transport

# search_read with no limit returns the whole table, which on a restored production
# database is the reliable way to hit the 120 s ceiling.
DEFAULT_LIMIT = 100

_DEFAULT_ATTRIBUTES = ["type", "string", "required", "readonly", "relation",
                       "selection", "store", "help"]


def _ready(instance):
    """(instance, uid, None) or (None, None, error). Resolve, then log in."""
    inst, err = config.resolve(instance)
    if err:
        return None, None, err
    uid, err = transport.authenticate(inst)
    if err:
        return None, None, err
    return inst, uid, None


# --- reads --------------------------------------------------------------------

def odoo_instances():
    """Every declared instance and how far it may be written to. Touches no server."""
    known, problem = config.load()
    if not known:
        return problem or config.nothing_declared()
    out = {"config_path": str(config.config_path()),
           "instances": [i.public() for _n, i in sorted(known.items())]}
    for entry in out["instances"]:
        # Whether the password is *reachable*, never what it is.
        name = entry["instance"]
        _pw, err = known[name].password()
        entry["password_available"] = err is None
    warning = config.file_mode_warning()
    if warning:
        out["warning"] = warning
    if problem:
        out["config_problem"] = problem
    return out


def odoo_ping(instance):
    """Who this instance is, and whether the credential works."""
    inst, err = config.resolve(instance)
    if err:
        return err
    ver, err = transport.version(inst)
    if err:
        return err
    uid, err = transport.authenticate(inst)
    if err:
        return err
    return {"ok": True, **inst.public(), "uid": uid,
            "version": (ver or {}).get("server_version"),
            "version_info": (ver or {}).get("server_version_info")}


def odoo_fields(instance, model, fields=None, attributes=None):
    """`fields_get` on the named instance, so the answer is that server's real schema."""
    inst, uid, err = _ready(instance)
    if err:
        return err
    out = transport.execute(inst, uid, model, "fields_get",
                            [list(fields or [])],
                            {"attributes": list(attributes or _DEFAULT_ATTRIBUTES)},
                            transport="xmlrpc")
    if "error" in out:
        return out
    got = out.get("result") or {}
    return {"instance": inst.name, "model": model, "count": len(got), "fields": got}


def odoo_search(instance, model, domain=None, fields=None, limit=DEFAULT_LIMIT,
                offset=0, order=None):
    """`search_read`, with the two limits that Odoo will not impose for you.

    `limit=0` never reaches Odoo. Zero is falsy there, so `_search` emits no `LIMIT`
    clause and `search_read(limit=0)` hands back the entire table — the opposite of what
    the number says. The tool turns it into a `search_count` and returns only the count.
    Omitting `limit` likewise means 100 here rather than "everything".
    """
    inst, uid, err = _ready(instance)
    if err:
        return err
    dom = list(domain or [])
    limit = DEFAULT_LIMIT if limit is None else int(limit)
    if limit < 0:
        return {"error": f"limit must be zero or more, got {limit}", "layer": "guard",
                "instance": inst.name, "model": model,
                "hint": "limit=0 counts the matches instead of reading them"}
    if limit == 0:
        out = transport.execute(inst, uid, model, "search_count", [dom],
                                transport="xmlrpc")
        if "error" in out:
            return out
        return {"instance": inst.name, "model": model, "domain": dom,
                "count": out.get("result")}
    kwargs = {"limit": limit, "offset": int(offset or 0)}
    if fields:
        kwargs["fields"] = list(fields)
    if order:
        kwargs["order"] = order
    out = transport.execute(inst, uid, model, "search_read", [dom], kwargs,
                            transport="xmlrpc")
    if "error" in out:
        return out
    rows = out.get("result") or []
    return {"instance": inst.name, "model": model, "domain": dom, "limit": limit,
            "offset": kwargs["offset"], "count": len(rows), "records": rows}


# --- writes -------------------------------------------------------------------

def odoo_create(instance, model, values, confirm=False):
    """Create one record. Refused unless the model is in this instance's write_models
    and `confirm=true` arrives in the same call."""
    inst, err = config.resolve(instance)
    if err:
        return err
    refusal = guard.check_model(inst, model)
    if refusal:
        return {**refusal, "values": values}
    refusal = guard.check_confirm(
        inst, confirm, f"create one {model} record on {inst.name!r} with "
                       f"{sorted((values or {}).keys())}",
        {"model": model, "values": values})
    if refusal:
        return refusal
    uid, err = transport.authenticate(inst)
    if err:
        return err
    out = transport.execute(inst, uid, model, "create", [dict(values or {})],
                            transport="jsonrpc")
    if "error" in out:
        return out
    return {"ok": True, "instance": inst.name, "model": model, "id": out.get("result")}


def odoo_write(instance, model, ids, values, confirm=False):
    """Write values onto existing ids. Same two conditions as `odoo_create`.

    A timeout here is an unknown state, not a rollback: nothing can tell "Odoo never
    saw it" from "Odoo committed and the answer was lost". Read the records back before
    retrying, and keep the id list small.
    """
    inst, err = config.resolve(instance)
    if err:
        return err
    ids = [int(i) for i in (ids or [])]
    refusal = guard.check_model(inst, model)
    if refusal:
        return {**refusal, "ids": ids, "values": values}
    refusal = guard.check_confirm(
        inst, confirm, f"write {values} onto {len(ids)} {model} record(s) on "
                       f"{inst.name!r}",
        {"model": model, "ids": ids, "values": values})
    if refusal:
        return refusal
    if not ids:
        return {"error": "no ids given", "layer": "guard", "instance": inst.name,
                "model": model}
    uid, err = transport.authenticate(inst)
    if err:
        return err
    out = transport.execute(inst, uid, model, "write", [ids, dict(values or {})],
                            transport="jsonrpc")
    if "error" in out:
        return out
    return {"ok": True, "instance": inst.name, "model": model, "ids": ids,
            "result": out.get("result")}


def odoo_call(instance, model, method, ids, args=None, kwargs=None, confirm=False):
    """Call a method on records. Gated on `model.method` being in write_methods.

    This is the tool for a multi-step business flow: there is no transaction across tool
    calls, so three calls are three transactions with no rollback between them. A flow
    that must be atomic belongs inside one method that manages its own transaction.

    `unlink` is refused here whatever the allowlist says.
    """
    inst, err = config.resolve(instance)
    if err:
        return err
    ids = [int(i) for i in (ids or [])]
    refusal = guard.check_method(inst, model, method)
    if refusal:
        return {**refusal, "ids": ids}
    refusal = guard.check_confirm(
        inst, confirm, f"call {model}.{method} on {len(ids)} record(s) on "
                       f"{inst.name!r}",
        {"model": model, "method": method, "ids": ids})
    if refusal:
        return refusal
    uid, err = transport.authenticate(inst)
    if err:
        return err
    out = transport.execute(inst, uid, model, method, [ids, *(args or [])],
                            dict(kwargs or {}), transport="jsonrpc")
    if "error" in out:
        return out
    # A method returning None comes back as {"ok": true, "result": null}. Over XML-RPC
    # that same call raises after the database has already changed.
    return {"ok": True, "instance": inst.name, "model": model, "method": method,
            "ids": ids, "result": out.get("result")}


_INSTANCE_PROP = {
    "type": "string",
    "description": "which declared server to talk to, by name. Required, always: there "
                   "is no default instance. Call odoo_instances for the valid names."}
_MODEL_PROP = {"type": "string", "description": "Odoo model name, e.g. res.partner"}
_CONFIRM_PROP = {
    "type": "boolean",
    "description": "must be true for the change to run. Default false, which returns a "
                   "refusal describing exactly what a confirmed call would do."}
_IDS_PROP = {"type": "array", "items": {"type": "integer"},
             "description": "record ids to act on"}

TOOLS = {
    "odoo_instances": (
        odoo_instances,
        "List the Odoo servers this machine may talk to, each with its database, login, "
        "the environment variable holding its password, and the models and methods it "
        "will accept writes for. Talks to no server, so it works even when they are all "
        "down, and it is where the instance names for every other tool come from.",
        {}, []),
    "odoo_ping": (
        odoo_ping,
        "Say who a named instance is: base URL, database, Odoo version, the uid the "
        "stored credential logs in as, and what may be written there. Use it before "
        "anything else against a server you have not touched this session, because it "
        "separates 'unreachable' from 'wrong password' from 'wrong database'.",
        {"instance": _INSTANCE_PROP}, ["instance"]),
    "odoo_fields": (
        odoo_fields,
        "Field metadata for one model as that specific server has it — type, label, "
        "required, readonly, relation, selection values. This is the live schema, so it "
        "differs from what the repository says whenever a module is not installed there "
        "or a studio field was added. Use it before writing values you have not written "
        "before.",
        {"instance": _INSTANCE_PROP, "model": _MODEL_PROP,
         "fields": {"type": "array", "items": {"type": "string"},
                    "description": "only these field names; omit for all of them"},
         "attributes": {"type": "array", "items": {"type": "string"},
                        "description": "which metadata keys to return; omit for a "
                                       "sensible set"}},
        ["instance", "model"]),
    "odoo_search": (
        odoo_search,
        "Read records with a domain, like search_read. limit defaults to 100 and "
        "limit=0 means 'just count them' — it runs search_count and returns no rows, "
        "because a zero limit passed to Odoo returns the entire table instead of "
        "nothing. Remember what the answer is about: on a restored database it "
        "describes the restore, which is not the state of production.",
        {"instance": _INSTANCE_PROP, "model": _MODEL_PROP,
         "domain": {"type": "array",
                    "description": "Odoo domain, e.g. [[\"name\", \"ilike\", \"acme\"]]. "
                                   "Omit for all records."},
         "fields": {"type": "array", "items": {"type": "string"},
                    "description": "field names to read; omit for the default set"},
         "limit": {"type": "integer",
                   "description": "how many records, default 100. Pass 0 to count "
                                  "instead of reading."},
         "offset": {"type": "integer", "description": "skip this many, default 0"},
         "order": {"type": "string", "description": "sort clause, e.g. \"id desc\""}},
        ["instance", "model"]),
    "odoo_create": (
        odoo_create,
        "Create one record. It runs only when the model is in that instance's "
        "write_models list and confirm=true is passed in the same call; without both it "
        "returns a refusal and opens no connection. Writes go over JSON-RPC because "
        "XML-RPC can report a failure on a call that already committed.",
        {"instance": _INSTANCE_PROP, "model": _MODEL_PROP,
         "values": {"type": "object", "description": "field name to value"},
         "confirm": _CONFIRM_PROP},
        ["instance", "model", "values"]),
    "odoo_write": (
        odoo_write,
        "Write values onto records that already exist, under the same two conditions as "
        "odoo_create. Keep the id list short: a call that times out leaves an unknown "
        "state, since nothing can distinguish a change Odoo never received from one it "
        "committed before the answer was lost. Read the records back before retrying.",
        {"instance": _INSTANCE_PROP, "model": _MODEL_PROP, "ids": _IDS_PROP,
         "values": {"type": "object", "description": "field name to value"},
         "confirm": _CONFIRM_PROP},
        ["instance", "model", "ids", "values"]),
    "odoo_call": (
        odoo_call,
        "Call a method on records — the way to run a business flow that has to be one "
        "transaction, since separate tool calls are separate transactions with no "
        "rollback between them. It runs only when \"model.method\" is listed in that "
        "instance's write_methods and confirm=true is passed. Deleting is refused here "
        "no matter what the list says.",
        {"instance": _INSTANCE_PROP, "model": _MODEL_PROP,
         "method": {"type": "string", "description": "method name to call on the ids"},
         "ids": _IDS_PROP,
         "args": {"type": "array", "description": "extra positional arguments after "
                                                  "the ids"},
         "kwargs": {"type": "object", "description": "keyword arguments"},
         "confirm": _CONFIRM_PROP},
        ["instance", "model", "method", "ids"]),
}
