from flightdeck.hub import registry
from flightdeck.hub.types import NodeDef, item

def test_register_and_get():
    d = NodeDef(type="dummy", label="Dummy", inputs=["main"], outputs=["main"],
                run=lambda params, inputs, ctx: [[item({"ok": True})]])
    registry.register(d)
    got = registry.get("dummy")
    assert got.label == "Dummy"
    assert got.outputs == ["main"]
    assert got.run({}, [], None) == [[{"json": {"ok": True}}]]

def test_unknown_type_raises():
    import pytest
    with pytest.raises(KeyError):
        registry.get("nope")
