"""Hub triggers: let a flow run because something happened, not because
someone clicked Run.

Two kinds only, per the owner's decision -- no cron, no scheduler:

  event    fires when a `flightdeck.events.BUS` event's `kind` starts with a
           configured prefix (the same prefix-match `BUS.on` already uses).
  webhook  fires on an authenticated-by-token `POST /api/hub/webhook/{token}`
           (see routers/hub.py).

A trigger is a small JSON row, persisted beside flows (same atomic
tmp-file-then-rename format `hub/store.py` uses for flows, just under a
`triggers/` subdirectory of `flows_dir` so trigger files never show up in
`store.list_flows`'s flat glob):

    { id, flow_id, kind, enabled, event_prefix?, token?,
      created_at, last_fired_at, last_run_id, last_error }

Wiring an in-process bus sink to a filesystem-backed registry and a
per-request FastAPI app needs one more piece than either alone: the sink
reacts to events with NO request in hand, so it cannot read
`request.app.state.flows_dir` the way every other Hub endpoint does. FlightDeck
is single-process / single-app by design (see events.py and runtime.py's
module docstrings), so `bind()` below lets the one HTTP-facing side
(routers/hub.py, via a router-level dependency) keep this module pointed at
whichever app is actually running -- including the throwaway app + tmp_path
each test spins up.

Three guards, all required by the plan, because "a flow can now run itself"
is exactly the kind of feature that must be impossible to run away with:

  - a kill switch (module flag AND env var) that stops every trigger with no
    redeploy;
  - one concurrent run per trigger, because `summary.updated` fires on every
    ingest tick and can easily outrun a flow that calls out over HTTP/XML-RPC
    -- a second event during an in-flight run is DROPPED and counted, never
    queued;
  - a trace per run, in the sense that every run's outcome (run id + error,
    if any) is written back onto the trigger row, because an automation
    nobody can inspect afterwards is not shippable.

Every run -- event or webhook -- emits `hub.trigger.fired` / `.failed` on the
bus, so the existing SSE surface shows trigger activity with no new plumbing.
"""
import copy
import json
import os
import re
import secrets
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional

from flightdeck.events import BUS
from flightdeck.hub import engine
from flightdeck.hub import store as flow_store

Trigger = Dict[str, Any]

FIRED = "hub.trigger.fired"
FAILED = "hub.trigger.failed"

# Every kind this module emits shares this prefix, and the event sink refuses
# all of them (see `_on_event`). Keeping it derived-by-convention rather than
# built from FIRED/FAILED means a THIRD kind added later is excluded the moment
# it is named, instead of the day someone remembers to update a tuple.
_OWN_KIND_PREFIX = "hub.trigger."

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


# ---------------------------------------------------------------- kill switch

# Module flag for tests/ops (flip in-process, no env mutation needed) ANDed
# with an env var an operator can set without touching code or redeploying --
# the whole point of a kill switch is that it still works when the thing
# going wrong is inside the process the flag lives in.
_enabled = True


def set_enabled(value: bool) -> None:
    """In-process half of the kill switch. Test/ops hook."""
    global _enabled
    _enabled = value


def triggers_disabled() -> bool:
    return not (_enabled and os.environ.get("FLIGHTDECK_TRIGGERS", "1") != "0")


# ------------------------------------------------------------------- binding

# The "current app" this module reacts on behalf of. Set by routers/hub.py on
# every Hub request (see `bind`'s docstring above for why a request-scoped
# assignment is enough in a single-process/single-app service).
_active_flows_dir: Optional[str] = None
_active_resolve_credential: Optional[Callable] = None


def bind(flows_dir: str, resolve_credential: Optional[Callable] = None) -> None:
    """Point the event-trigger sink at `flows_dir` / `resolve_credential`."""
    global _active_flows_dir, _active_resolve_credential
    _active_flows_dir = flows_dir
    _active_resolve_credential = resolve_credential


# --------------------------------------------------------------- persistence

def _validate_id(trigger_id) -> str:
    if not isinstance(trigger_id, str) or not _SAFE_ID.match(trigger_id):
        raise ValueError("invalid trigger id: %r" % (trigger_id,))
    return trigger_id


def _triggers_dir(flows_dir: str) -> str:
    return os.path.join(flows_dir, "triggers")


def _path(flows_dir: str, trigger_id: str) -> str:
    return os.path.join(_triggers_dir(flows_dir), _validate_id(trigger_id) + ".json")


def list_triggers(flows_dir: str) -> List[Trigger]:
    out: List[Trigger] = []
    d = _triggers_dir(flows_dir)
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name)) as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


def load_trigger(flows_dir: str, trigger_id: str) -> Optional[Trigger]:
    path = _path(flows_dir, trigger_id)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def save_trigger(flows_dir: str, trigger: Trigger) -> Trigger:
    d = _triggers_dir(flows_dir)
    os.makedirs(d, exist_ok=True)
    if not trigger.get("id"):
        trigger["id"] = uuid.uuid4().hex
    dest = _path(flows_dir, trigger["id"])
    tmp = dest + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(trigger, fh, indent=2)
    os.replace(tmp, dest)
    return trigger


def delete_trigger(flows_dir: str, trigger_id: str) -> None:
    path = _path(flows_dir, trigger_id)
    if os.path.exists(path):
        os.remove(path)


def create_trigger(flows_dir: str, flow_id: str, kind: str, *,
                    enabled: bool = True, event_prefix: Optional[str] = None,
                    token: Optional[str] = None) -> Trigger:
    """Build and persist a new trigger row. `token` is generated with
    `secrets.token_urlsafe` when a webhook trigger doesn't supply one."""
    if kind not in ("event", "webhook"):
        raise ValueError("unknown trigger kind: %r" % (kind,))
    trigger: Trigger = {
        "id": uuid.uuid4().hex,
        "flow_id": flow_id,
        "kind": kind,
        "enabled": enabled,
        "event_prefix": event_prefix if kind == "event" else None,
        "token": (token or secrets.token_urlsafe(32)) if kind == "webhook" else None,
        "created_at": time.time(),
        "last_fired_at": None,
        "last_run_id": None,
        "last_error": None,
    }
    return save_trigger(flows_dir, trigger)


def find_webhook_trigger(flows_dir: str, token: str) -> Optional[Trigger]:
    """The enabled webhook trigger for `token`, or None.

    "No such token" and "token belongs to a disabled trigger" collapse into
    the same None on purpose: the route maps both to an identical 404, so a
    probing client can never learn whether a token exists.
    """
    if not token:
        return None
    for trig in list_triggers(flows_dir):
        if trig.get("kind") == "webhook" and trig.get("enabled") and trig.get("token") == token:
            return trig
    return None


# ---------------------------------------------------------- run + guard + go

class _ReentrancyGuard:
    """At most one in-flight run per trigger id.

    `try_acquire` is non-blocking: the caller (the bus dispatch thread for an
    event trigger, the request thread for a webhook) must never block on a
    slow flow, it must know IMMEDIATELY whether to drop this occurrence. A
    dropped attempt is counted, never queued -- queuing would let a stuck flow
    build an unbounded backlog behind a high-frequency kind like
    `summary.updated`.
    """

    def __init__(self) -> None:
        self._locks: Dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self.dropped: Dict[str, int] = {}

    def try_acquire(self, trigger_id: str) -> bool:
        with self._meta_lock:
            lock = self._locks.setdefault(trigger_id, threading.Lock())
        acquired = lock.acquire(blocking=False)
        if not acquired:
            with self._meta_lock:
                self.dropped[trigger_id] = self.dropped.get(trigger_id, 0) + 1
        return acquired

    def release(self, trigger_id: str) -> None:
        with self._meta_lock:
            lock = self._locks.get(trigger_id)
        if lock is not None:
            lock.release()


_guard = _ReentrancyGuard()


def dropped_count(trigger_id: str) -> int:
    return _guard.dropped.get(trigger_id, 0)


def _seed_start_nodes(flow: dict, seed: dict) -> dict:
    """A deep copy of `flow` with every `start` node's `params.seed` set to
    `seed`, so a triggered run carries the event/webhook payload the same way
    a manual run carries whatever seed the flow author typed in.

    `engine.run_flow` also accepts a `seed=` keyword, which this module passes
    through too, but that parameter is currently unused by the engine -- the
    Start node only ever reads `params["seed"]` (hub/nodes/basic.py,
    `start_execute`). Since `hub/engine.py` is out of scope for this track,
    mutating the Start node's own params is the one path that actually
    delivers the seed today. Always a COPY: a trigger must never rewrite the
    saved flow file on disk.
    """
    seeded = copy.deepcopy(flow)
    for node in seeded.get("nodes", []):
        if node.get("type") == "start":
            node.setdefault("params", {})["seed"] = seed
    return seeded


def _record_outcome(flows_dir: str, trigger_id: str, run_id: str, error: Optional[str]) -> None:
    fresh = load_trigger(flows_dir, trigger_id)
    if fresh is None:
        return  # trigger was deleted while its run was in flight
    fresh["last_run_id"] = run_id
    fresh["last_error"] = error
    fresh["last_fired_at"] = time.time()
    try:
        save_trigger(flows_dir, fresh)
    except Exception:
        traceback.print_exc()


def _run_and_record(flows_dir: str, trigger: Trigger, seed: dict,
                     resolve_credential: Optional[Callable], source: str, run_id: str) -> None:
    """Runs on a worker thread (see `dispatch`). Never raises: a broken flow
    or a broken trigger row must not kill the thread silently -- it must land
    in `last_error` and on the bus."""
    trigger_id = trigger["id"]
    error: Optional[str] = None
    try:
        flow = flow_store.load_flow(flows_dir, trigger["flow_id"])
        if flow is None:
            raise ValueError("trigger %r points at missing flow %r" %
                              (trigger_id, trigger.get("flow_id")))
        seeded = _seed_start_nodes(flow, seed)
        result = engine.run_flow(seeded, seed=seed, resolve_credential=resolve_credential)
        if result["status"] != "ok":
            node_errors = [f"{nid}: {info['error']}" for nid, info in result["nodes"].items()
                           if info.get("status") == "error"]
            error = "; ".join(node_errors) or result.get("error") or "flow run failed"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    _record_outcome(flows_dir, trigger_id, run_id, error)
    payload = {"trigger_id": trigger_id, "trigger_kind": trigger.get("kind"),
               "flow_id": trigger.get("flow_id"), "run_id": run_id}
    if error:
        BUS.emit(FAILED, source=source, error=error, **payload)
    else:
        BUS.emit(FIRED, source=source, **payload)


def dispatch(flows_dir: str, trigger: Trigger, seed: dict,
             resolve_credential: Optional[Callable] = None,
             source: str = "hub-trigger") -> Optional[str]:
    """Try to run `trigger`'s flow with `seed` in a background thread.

    Returns the run id on success, or None if the run was dropped -- either
    the kill switch is active, or a previous run for this trigger is still in
    flight (see `dropped_count`). The guard is checked and acquired on the
    CALLING thread (synchronously, non-blocking) so a caller that decides not
    to run never pays for a thread spawn; only the flow itself -- the part
    that can be slow (an HTTP or XML-RPC node) -- runs off-thread.
    """
    if triggers_disabled():
        return None
    trigger_id = trigger["id"]
    if not _guard.try_acquire(trigger_id):
        print(f"[hub.triggers] dropped run for trigger {trigger_id}: previous run "
              f"still in flight (dropped so far: {dropped_count(trigger_id)})")
        return None
    run_id = uuid.uuid4().hex

    def _worker() -> None:
        try:
            _run_and_record(flows_dir, trigger, seed, resolve_credential, source, run_id)
        finally:
            _guard.release(trigger_id)

    threading.Thread(target=_worker, daemon=True).start()
    return run_id


# --------------------------------------------------------------- event sink

def _on_event(ev) -> None:
    """The ONE sink registered on the bus for every event-kind trigger.

    A per-trigger `BUS.on` was considered and rejected: the trigger registry
    changes at runtime (create/enable/disable), and `BUS.on` returns an `off`
    function the caller is responsible for -- keeping N of those in sync with
    N rows on every edit is exactly the kind of bookkeeping a single sink that
    re-reads the registry per event avoids outright. Runs ON THE EVENT LOOP
    (see events.py's `Bus.on` docstring): the registry read here is a handful
    of small JSON files and stays synchronous; only `run_flow` -- the part
    that can actually be slow -- is handed off to a thread, via `dispatch`.
    """
    if triggers_disabled():
        return
    # This module's OWN kinds never feed this module. Structural, not
    # configurable, because the reentrancy guard cannot cover this case:
    #
    #   `_run_and_record` emits FIRED from the worker thread, and with a bound
    #   loop `Bus.emit` only SCHEDULES the dispatch (`call_soon_threadsafe`).
    #   The worker's `finally: _guard.release()` therefore runs BEFORE the sink
    #   ever sees FIRED, so `try_acquire` succeeds and a trigger whose prefix
    #   matches `hub.trigger.` re-runs itself, forever.
    #
    # It looks safe under pytest only because this suite leaves `BUS._loop` as
    # None (see tests/hub/test_triggers.py's fixture), which makes `emit`
    # synchronous and keeps the sink inside the lock — the one dimension where
    # the test environment differs from production. Hence a guard here rather
    # than a note telling whoever configures a trigger to avoid a prefix.
    if ev.kind.startswith(_OWN_KIND_PREFIX):
        return
    flows_dir = _active_flows_dir
    if not flows_dir:
        return  # no app has called `bind()` yet
    try:
        rows = list_triggers(flows_dir)
    except Exception:
        traceback.print_exc()
        return
    envelope = ev.as_dict()  # {kind, at, source, payload} -- the Item.json a flow reads
    resolve_credential = _active_resolve_credential
    for trig in rows:
        if trig.get("kind") != "event" or not trig.get("enabled"):
            continue
        if not ev.kind.startswith(trig.get("event_prefix") or ""):
            continue
        dispatch(flows_dir, trig, envelope, resolve_credential, source="event")


_off_sink: Optional[Callable[[], None]] = None


def install() -> None:
    """(Re)install the single module-wide sink on the shared bus.

    Idempotent and safe to call more than once: it uninstalls any previous
    registration first. That matters because the global bus can be reset out
    from under this module -- another track's test fixture calls
    `BUS.reset()` between tests (see tests/test_decisions.py) -- and without
    this, that reset would silently orphan the trigger sink for the rest of
    the process. Called once below at import time, and defensively at the top
    of this module's own tests.
    """
    global _off_sink
    if _off_sink is not None:
        _off_sink()
    _off_sink = BUS.on("", _on_event)


install()
