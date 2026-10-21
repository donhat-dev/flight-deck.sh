"""Integration Hub: flow builder backend (node types, presets, flows,
credentials, run).

Flows are files under `app.state.flows_dir`; credentials live in the shared
SQLite DB (see hub/store.py, hub/credentials.py). Credential writes serialize on
the same `runtime.lock` the watcher/ingest hold, so they never interleave with
an in-flight ingest transaction on the shared write connection.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from flightdeck import db
from flightdeck.hub import (credentials, engine, presets as hub_presets,
                             registry as hub_registry, store, triggers)


def _resolve_cred_factory(request: Request):
    # Injected into engine.run_flow so nodes can resolve a credential reference
    # to its data dict. Merges "kind" into the data dict (not stored there) so
    # nodes can branch on cred["kind"] without a second lookup. Short-lived read
    # connection, same pattern as every other per-request DB access.
    def _resolve(ref):
        if not ref:
            return None
        c = db.open_read(request.app.state.cfg["db_path"])
        try:
            cred = credentials.get(c, ref)
        finally:
            c.close()
        if not cred:
            return None
        d = dict(cred["data"])
        d["kind"] = cred["kind"]
        return d
    return _resolve


def _bind_triggers(request: Request) -> None:
    # Keep flightdeck.hub.triggers pointed at THIS app's flows_dir/credential
    # resolver. The trigger event sink reacts to bus events with no request of
    # its own to read app.state from, so something has to hand it that context
    # -- see triggers.py's module docstring for why a router-level dependency
    # (re-bind on every Hub request) is enough given FlightDeck's single-
    # process/single-app design, including the throwaway app + tmp_path each
    # test spins up.
    triggers.bind(request.app.state.flows_dir, _resolve_cred_factory(request))


router = APIRouter(tags=["hub"], dependencies=[Depends(_bind_triggers)])


def _require_flow_shape(flow):
    # Untyped dict bodies: a malformed flow must be a 400, not an uncaught
    # KeyError -> 500 deeper in engine.run_flow. Validate not just the top
    # containers but each element's shape, because run_flow dereferences
    # n["id"]/n["type"] and c["from"]/c["to"] BEFORE its per-node try/except.
    ok = (isinstance(flow, dict)
          and isinstance(flow.get("nodes"), list)
          and isinstance(flow.get("connections"), list)
          and all(isinstance(n, dict)
                  and isinstance(n.get("id"), str)
                  and isinstance(n.get("type"), str)
                  for n in flow["nodes"])
          and all(isinstance(c, dict)
                  and isinstance(c.get("from"), (list, tuple)) and len(c["from"]) >= 2
                  and isinstance(c.get("to"), (list, tuple)) and len(c["to"]) >= 2
                  for c in flow["connections"]))
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=("flow requires nodes [{id, type}] and "
                    "connections [{from:[id,idx], to:[id,idx]}]"))
    return flow


@router.get("/api/hub/node-types")
def hub_node_types():
    return [{"type": d.type, "label": d.label, "inputs": d.inputs,
             "outputs": d.outputs, "params": d.params_schema}
            for d in hub_registry.all_defs()]


@router.get("/api/hub/presets")
def hub_presets_ep():
    return hub_presets.presets()


@router.get("/api/hub/flows")
def hub_flows(request: Request):
    return store.list_flows(request.app.state.flows_dir)


@router.get("/api/hub/flows/{flow_id}")
def hub_flow_get(request: Request, flow_id: str):
    try:
        found = store.load_flow(request.app.state.flows_dir, flow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid flow id")
    if found is None:
        raise HTTPException(status_code=404, detail="flow not found")
    return found


@router.post("/api/hub/flows")
def hub_flow_save(request: Request, flow: dict):
    _require_flow_shape(flow)
    try:
        return store.save_flow(request.app.state.flows_dir, flow)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid flow id")


@router.delete("/api/hub/flows/{flow_id}")
def hub_flow_delete(request: Request, flow_id: str):
    try:
        store.delete_flow(request.app.state.flows_dir, flow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid flow id")
    return {"ok": True}


@router.get("/api/hub/credentials")
def hub_creds(request: Request):
    c = db.open_read(request.app.state.cfg["db_path"])
    try:
        return credentials.list(c)
    finally:
        c.close()


@router.post("/api/hub/credentials")
def hub_cred_save(request: Request, cred: dict):
    if not (isinstance(cred, dict)
            and isinstance(cred.get("name"), str)
            and isinstance(cred.get("kind"), str)
            and isinstance(cred.get("data"), dict)):
        raise HTTPException(
            status_code=400, detail="credential requires name, kind, data")
    # write_conn is the shared single-writer connection (watcher/ingest use it
    # under the runtime lock); serialize credential writes on the same lock so
    # they never interleave with an in-flight ingest transaction.
    with request.app.state.runtime.lock:
        return credentials.upsert(request.app.state.write_conn, cred)


@router.delete("/api/hub/credentials/{cred_id}")
def hub_cred_delete(request: Request, cred_id: str):
    with request.app.state.runtime.lock:
        credentials.delete(request.app.state.write_conn, cred_id)
    return {"ok": True}


@router.post("/api/hub/run")
def hub_run(request: Request, body: dict):
    flow = body.get("flow")
    if not flow and body.get("flowId"):
        try:
            flow = store.load_flow(request.app.state.flows_dir, body["flowId"])
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid flow id")
        if flow is None:
            raise HTTPException(status_code=404, detail="flow not found")
    if not flow:
        raise HTTPException(status_code=400, detail="flow or flowId required")
    _require_flow_shape(flow)
    return engine.run_flow(flow, seed=body.get("seed"),
                           resolve_credential=_resolve_cred_factory(request))


@router.post("/api/hub/webhook/{token}")
def hub_webhook(request: Request, token: str, body: Optional[dict] = None):
    # Kill switch first: an operator who has decided every trigger must stop
    # gets a clear 503, not a silent no-op that leaves them guessing whether
    # the call even reached the process.
    if triggers.triggers_disabled():
        raise HTTPException(status_code=503, detail="hub triggers are disabled")
    flows_dir = request.app.state.flows_dir
    trig = triggers.find_webhook_trigger(flows_dir, token)
    if trig is None:
        # Unknown token and disabled-trigger token produce the EXACT same
        # response (see triggers.find_webhook_trigger) so a probing client
        # cannot learn whether a token exists by watching for a status change.
        raise HTTPException(status_code=404, detail="not found")
    # Fire-and-forget: the caller gets {ok, run_id} the instant a thread is
    # dispatched, never waiting on the flow itself (a webhook is by nature an
    # external caller who may time out long before a flow with an HTTP/XML-RPC
    # node completes).
    run_id = triggers.dispatch(flows_dir, trig, body or {},
                               resolve_credential=_resolve_cred_factory(request),
                               source="webhook")
    if run_id is None:
        # Only reachable if a previous run for this trigger is still in
        # flight -- the kill switch was already handled above as a 503.
        return {"ok": False, "run_id": None}
    return {"ok": True, "run_id": run_id}
