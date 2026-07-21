from flightdeck.hub import engine, registry
from flightdeck.hub.types import NodeDef, item

def _register_test_nodes():
    registry.register(NodeDef("t.start", "Start", [], ["main"],
        run=lambda p, ins, ctx: [[item({"seed": 1})]]))
    # emits its input's json.n doubled, onto socket 0 if even else socket 1
    registry.register(NodeDef("t.route", "Route", ["main"], ["even", "odd"],
        run=lambda p, ins, ctx: (
            [[item({"n": it["json"]["n"] * 2})], []] if it["json"]["n"] % 2 == 0
            else [[], [item({"n": it["json"]["n"]})]]
        ) if (it := (ins[0][0] if ins and ins[0] else item({"n": 0}))) else [[], []]))
    registry.register(NodeDef("t.sink", "Sink", ["main"], ["main"],
        run=lambda p, ins, ctx: [[item({"got": ins[0][0]["json"]})]] if ins and ins[0] else [[]]))

def test_routes_items_to_correct_socket_and_traces():
    _register_test_nodes()
    flow = {
        "id": "f1", "name": "t",
        "nodes": [
            {"id": "s", "type": "t.start", "params": {}},
            {"id": "r", "type": "t.route", "params": {}},
            {"id": "even", "type": "t.sink", "params": {}},
            {"id": "odd", "type": "t.sink", "params": {}},
        ],
        "connections": [
            {"from": ["s", 0], "to": ["r", 0]},
            {"from": ["r", 0], "to": ["even", 0]},   # even socket
            {"from": ["r", 1], "to": ["odd", 0]},    # odd socket
        ],
    }
    # seed makes start emit n; override start to carry n=4 (even)
    registry.register(NodeDef("t.start", "Start", [], ["main"],
        run=lambda p, ins, ctx: [[item({"n": 4})]]))
    res = engine.run_flow(flow)
    assert res["status"] == "ok"
    assert res["nodes"]["even"]["status"] == "ok"
    assert res["nodes"]["even"]["outputsPerSocket"][0][0]["json"]["got"]["n"] == 8
    # odd sink received nothing -> not run
    assert res["nodes"]["odd"]["status"] == "skipped"

def test_unknown_node_type_errors_not_crash():
    _register_test_nodes()
    flow = {
        "id": "f2", "name": "unknown-type",
        "nodes": [
            {"id": "bad", "type": "t.does_not_exist", "params": {}},
            {"id": "good", "type": "t.start", "params": {}},
        ],
        "connections": [],
    }
    res = engine.run_flow(flow)  # must not raise
    assert res["nodes"]["bad"]["status"] == "error"
    assert res["nodes"]["good"]["status"] == "ok"
    assert res["status"] == "error"

def test_executor_exception_becomes_error_trace():
    _register_test_nodes()
    def _boom(p, ins, ctx):
        raise RuntimeError("boom")
    registry.register(NodeDef("t.boom", "Boom", [], ["main"], run=_boom))
    flow = {
        "id": "f3", "name": "executor-raises",
        "nodes": [{"id": "b", "type": "t.boom", "params": {}}],
        "connections": [],
    }
    res = engine.run_flow(flow)  # must not raise
    assert res["nodes"]["b"]["status"] == "error"
    assert "boom" in res["nodes"]["b"]["error"]
    assert res["status"] == "error"

def test_cycle_detected():
    _register_test_nodes()
    flow = {
        "id": "f4", "name": "cycle",
        "nodes": [
            {"id": "a", "type": "t.sink", "params": {}},
            {"id": "b", "type": "t.sink", "params": {}},
        ],
        "connections": [
            {"from": ["a", 0], "to": ["b", 0]},
            {"from": ["b", 0], "to": ["a", 0]},
        ],
    }
    res = engine.run_flow(flow)
    assert res["status"] == "error"
    assert res.get("error")
    assert res["nodes"]["a"]["status"] == "skipped"
    assert res["nodes"]["b"]["status"] == "skipped"
