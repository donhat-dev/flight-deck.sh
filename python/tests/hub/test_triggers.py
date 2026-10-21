"""Hub triggers: an event or a webhook running a flow with no click involved.

Threads plus a synchronous bus dispatch (see events.py: the test suite never
binds BUS to a real loop, so `BUS.emit` calls sinks directly on the calling
thread) are the fiddly part here. Two rules keep this deterministic instead of
sleep-based:

  - A trigger's OUTCOME is observed via the `hub.trigger.fired` / `.failed`
    bus events triggers.py itself emits once a run finishes -- a
    `threading.Event` set from that sink's callback is a real completion
    signal, not a guess about how long a flow takes.
  - A trigger's IN-FLIGHT state (for the reentrancy test) is observed by
    monkeypatching the `http` node's transport to block on a
    `threading.Event` the test controls, exactly like test_flow_e2e.py mocks
    it, so "still running" is a fact the test set up rather than a timing
    assumption.

No new node types: every synthetic flow below uses only `start`, `set`, and
`http` (mocked), which is enough to prove the mechanism (matching against the
registry, seeding, guarding, tracing) without inventing a product use case.
"""
import asyncio
import threading
import time

import pytest

from flightdeck.events import BUS
from flightdeck.hub import triggers
from flightdeck.hub.nodes import http, load


@pytest.fixture(autouse=True)
def _hub_triggers_isolated(monkeypatch, tmp_path):
    """Make every test in this file independent of bus/global state left by
    whatever ran before or will run after it.

    - `BUS._loop` may be left pointing at a CLOSED event loop by another test
      file's `with TestClient(app) as c:` fixture (lifespan binds the loop;
      nothing unbinds it on exit). Forcing it back to None keeps `BUS.emit`
      synchronous here, which is also the real behavior the CLI and this test
      suite run under (see events.py).
    - `triggers.install()` is idempotent (see its docstring) and re-asserts
      this module's sink even if another test file's `BUS.reset()` wiped it.
    - `set_enabled(True)` and a fresh `tmp_path` as flows_dir undo whatever a
      previous test in THIS file did (kill switch, trigger rows).
    """
    monkeypatch.setattr(BUS, "_loop", None, raising=False)
    triggers.install()
    triggers.set_enabled(True)
    load.load_all()
    flows_dir = str(tmp_path)
    triggers.bind(flows_dir, None)
    yield flows_dir


def _save_flow(flows_dir, flow):
    from flightdeck.hub import store
    return store.save_flow(flows_dir, flow)


def _start_and_http_flow(url="http://svc/whoami"):
    """start -> http. The http node's URL/headers are ZEN-templated from
    `$json`, which is the start node's output item -- i.e. whatever seed a
    trigger injects. No new node types: both already exist."""
    return {
        "id": "f-" + url.replace("/", "").replace(":", ""),
        "name": "probe",
        "nodes": [
            {"id": "s", "type": "start", "label": "Start", "params": {"seed": {}}},
            {"id": "h", "type": "http", "label": "Call",
             "params": {"method": "GET", "url": url,
                        "headers": {"X-Kind": "{{ $json.kind }}",
                                    "X-Source": "{{ $json.source }}"}}},
        ],
        "connections": [{"from": ["s", 0], "to": ["h", 0]}],
    }


def _wait_for(prefix, timeout=2.0):
    """Subscribe to `prefix` on the bus and return (events, wait_fn, off).
    `wait_fn(n)` blocks until at least `n` matching events arrived."""
    events = []
    got = threading.Event()

    def _cb(ev):
        events.append(ev)
        got.set()

    off = BUS.on(prefix, _cb)

    def _wait(n=1, timeout=timeout):
        deadline = time.time() + timeout
        while len(events) < n and time.time() < deadline:
            got.wait(max(0.0, deadline - time.time()))
            got.clear()
        return len(events) >= n

    return events, _wait, off


# --------------------------------------------------------------------- event

def test_event_trigger_fires_for_matching_prefix_and_not_for_other(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        return 200, {}, {"ok": True}

    monkeypatch.setattr(http, "_urllib_transport", transport)
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="demo.")

    fired, wait_fired, off = _wait_for(triggers.FIRED)
    try:
        BUS.emit("other.thing", source="test")   # must NOT match "demo."
        assert triggers.dropped_count(trig["id"]) == 0
        assert calls == []

        BUS.emit("demo.thing", source="test")
        assert wait_fired(1), "matching event should have fired the trigger"
        assert calls == [flow["nodes"][1]["params"]["url"]]
    finally:
        off()


def test_disabled_trigger_does_not_fire(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    calls = []
    monkeypatch.setattr(http, "_urllib_transport",
                        lambda *a, **k: calls.append(1) or (200, {}, {}))
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "event",
                                   event_prefix="demo.", enabled=False)

    BUS.emit("demo.thing", source="test")
    assert calls == []
    assert triggers.dropped_count(trig["id"]) == 0


def test_kill_switch_flag_suppresses_everything(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    calls = []
    monkeypatch.setattr(http, "_urllib_transport",
                        lambda *a, **k: calls.append(1) or (200, {}, {}))
    flow = _save_flow(flows_dir, _start_and_http_flow())
    triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="")

    monkeypatch.setattr(triggers, "_enabled", False)
    BUS.emit("anything.at.all", source="test")
    assert calls == []


def test_kill_switch_env_var_suppresses_everything(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    calls = []
    monkeypatch.setattr(http, "_urllib_transport",
                        lambda *a, **k: calls.append(1) or (200, {}, {}))
    flow = _save_flow(flows_dir, _start_and_http_flow())
    triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="")

    monkeypatch.setenv("FLIGHTDECK_TRIGGERS", "0")
    BUS.emit("anything.at.all", source="test")
    assert calls == []


def test_reentrancy_second_event_dropped_while_run_in_flight(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    started = threading.Event()
    proceed = threading.Event()

    def blocking_transport(method, url, headers, body, timeout):
        started.set()
        proceed.wait(5)
        return 200, {}, {"ok": True}

    monkeypatch.setattr(http, "_urllib_transport", blocking_transport)
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="demo.")

    fired, wait_fired, off = _wait_for(triggers.FIRED)
    try:
        BUS.emit("demo.go", source="test")
        assert started.wait(2.0), "first run should have reached the http node"

        # A second matching event while the first run is still blocked in the
        # http node: must be dropped, not queued, and counted.
        BUS.emit("demo.go", source="test")
        assert triggers.dropped_count(trig["id"]) == 1

        proceed.set()   # let the first run finish
        assert wait_fired(1)
        assert len(fired) == 1, "the dropped occurrence must never itself run"
    finally:
        off()


def test_flow_error_records_last_error_and_does_not_kill_the_sink(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="demo.")

    def raising_transport(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(http, "_urllib_transport", raising_transport)

    failed, wait_failed, off_failed = _wait_for(triggers.FAILED)
    try:
        BUS.emit("demo.go", source="test")
        assert wait_failed(1), "a raising node must record failure, not hang"
        assert "boom" in failed[0].payload["error"]
        row = triggers.load_trigger(flows_dir, trig["id"])
        assert row["last_error"] and "boom" in row["last_error"]
        assert row["last_run_id"] == failed[0].payload["run_id"]
    finally:
        off_failed()

    # The sink itself must still be alive: a second, healthy event on the SAME
    # sink must fire normally right after the failure above.
    monkeypatch.setattr(http, "_urllib_transport",
                        lambda *a, **k: (200, {}, {"ok": True}))
    fired, wait_fired, off_fired = _wait_for(triggers.FIRED)
    try:
        BUS.emit("demo.go", source="test")
        assert wait_fired(1), "the sink must keep serving other/later events"
    finally:
        off_fired()


def test_event_envelope_reaches_flow_as_seed(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    captured = []

    def transport(method, url, headers, body, timeout):
        captured.append(dict(headers))
        return 200, {}, {"ok": True}

    monkeypatch.setattr(http, "_urllib_transport", transport)
    flow = _save_flow(flows_dir, _start_and_http_flow())
    triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="demo.")

    fired, wait_fired, off = _wait_for(triggers.FIRED)
    try:
        BUS.emit("demo.seeded", source="unit-test")
        assert wait_fired(1)
    finally:
        off()

    assert captured[0]["X-Kind"] == "demo.seeded"
    assert captured[0]["X-Source"] == "unit-test"


# ------------------------------------------------------------------- webhook

def _webhook_client(flows_dir, monkeypatch):
    monkeypatch.setenv("TOKEN_AUDIT_FLOWS_DIR", flows_dir)
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", "config.toml")
    from fastapi.testclient import TestClient
    from flightdeck.server import create_app
    return TestClient(create_app())


def test_webhook_valid_token_runs(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    captured = []

    def transport(method, url, headers, body, timeout):
        captured.append(dict(headers))
        return 200, {}, {"ok": True}

    monkeypatch.setattr(http, "_urllib_transport", transport)
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "webhook")

    fired, wait_fired, off = _wait_for(triggers.FIRED)
    try:
        c = _webhook_client(flows_dir, monkeypatch)
        resp = c.post(f"/api/hub/webhook/{trig['token']}", json={"kind": "webhook.call"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["run_id"]
        assert wait_fired(1)
    finally:
        off()
    assert captured[0]["X-Kind"] == "webhook.call"


def test_webhook_unknown_token_is_404(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    c = _webhook_client(flows_dir, monkeypatch)
    resp = c.post("/api/hub/webhook/does-not-exist", json={})
    assert resp.status_code == 404


def test_webhook_disabled_token_is_404_with_identical_body(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "webhook", enabled=False)

    c = _webhook_client(flows_dir, monkeypatch)
    unknown = c.post("/api/hub/webhook/not-a-real-token", json={})
    disabled = c.post(f"/api/hub/webhook/{trig['token']}", json={})
    assert unknown.status_code == disabled.status_code == 404
    assert unknown.json() == disabled.json()


def test_webhook_kill_switch_returns_503(_hub_triggers_isolated, monkeypatch):
    flows_dir = _hub_triggers_isolated
    flow = _save_flow(flows_dir, _start_and_http_flow())
    trig = triggers.create_trigger(flows_dir, flow["id"], "webhook")

    monkeypatch.setenv("FLIGHTDECK_TRIGGERS", "0")
    c = _webhook_client(flows_dir, monkeypatch)
    resp = c.post(f"/api/hub/webhook/{trig['token']}", json={})
    assert resp.status_code == 503


# ------------------------------------------------- self-retrigger, bound loop

def test_own_kinds_never_retrigger_even_with_a_bound_loop(_hub_triggers_isolated):
    """A trigger broad enough to match `hub.trigger.fired` must not re-run itself.

    This is the ONE test in this file that binds BUS to a real loop, because
    the loop is what makes the bug possible and the rest of the file's
    `BUS._loop = None` fixture hides it:

      - unbound (the rest of this file): `emit` calls sinks synchronously on
        the worker thread, so the sink sees FIRED while `_guard` is still held
        and the reentrancy guard drops the re-run. Looks safe.
      - bound (production): `emit` only schedules the dispatch, so the
        worker's `finally: release()` has already run by the time the sink sees
        FIRED. `try_acquire` succeeds and the trigger re-runs itself forever.

    So the guard cannot be the reentrancy lock; it has to be `_on_event`
    refusing `_OWN_KIND_PREFIX` outright. Remove that check and this test does
    not merely fail — `fired` grows for the whole sleep window.
    """
    flows_dir = _hub_triggers_isolated
    # A start-only flow: enough to complete a run and emit FIRED, with no node
    # to mock. The bug under test is in the sink, not in any node.
    flow = {"id": "f-selfloop", "name": "selfloop",
            "nodes": [{"id": "s", "type": "start", "label": "Start",
                       "params": {"seed": {}}}],
            "connections": []}
    _save_flow(flows_dir, flow)
    # `event_prefix=""` is the broadest a misconfiguration can get, and it is
    # what an operator would reach for to mean "everything".
    triggers.create_trigger(flows_dir, flow["id"], "event", event_prefix="")

    fired = []
    off = BUS.on(triggers.FIRED, fired.append)

    async def scenario():
        BUS.bind(asyncio.get_running_loop())
        BUS.emit("summary.updated", source="test")
        # One runaway cycle is a thread spawn plus a loop tick — well under a
        # millisecond for a start-only flow — so 0.4s is hundreds of cycles'
        # worth of room, not a race with a slow first run.
        await asyncio.sleep(0.4)

    try:
        asyncio.run(scenario())
    finally:
        off()
        # Undo the bind: a closed loop left on the module-level bus would break
        # every test that runs after this one.
        BUS._loop = None

    assert len(fired) == 1, (
        f"expected exactly one run, saw {len(fired)} — the sink is feeding itself")
