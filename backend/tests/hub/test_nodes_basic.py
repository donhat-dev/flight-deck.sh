from flightdeck.hub.nodes import basic
from flightdeck.hub.engine import RunContext
from flightdeck.hub.types import item

def test_start_emits_seed():
    out = basic.start_execute({"seed": {"deal_id": 5}}, [], RunContext())
    assert out == [[{"json": {"deal_id": 5}}]]

def test_set_merges_and_sets_vars():
    ctx = RunContext()
    out = basic.set_execute(
        {"assignments": [{"name": "discounted", "expression": "$json.price * 0.9"}]},
        [[item({"price": 100})]], ctx)
    assert out[0][0]["json"]["discounted"] == 90
    assert ctx.vars["discounted"] == 90

def test_condition_routes_true_false():
    ctx = RunContext()
    t = basic.condition_execute({"expression": "$json.status == 200"},
                                [[item({"status": 200})]], ctx)
    assert t[0] and not t[1]
    f = basic.condition_execute({"expression": "$json.status == 200"},
                                [[item({"status": 500})]], ctx)
    assert not f[0] and f[1]
