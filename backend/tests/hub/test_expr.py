from flightdeck.hub import expr

CTX = {"$json": {"id": 7, "name": "x"}, "$node": {"Login": {"json": {"token": "abc"}}},
       "$vars": {"rate": 0.9}, "$run": {"id": "r1"}}

def test_evaluate_scalar():
    assert expr.evaluate("$json.id * 2", CTX) == 14

def test_interpolate_whole_string_returns_raw_type():
    # exactly one expression -> keep the native type (int), not "7"
    assert expr.interpolate("{{ $json.id }}", CTX) == 7

def test_interpolate_embedded_returns_string():
    assert expr.interpolate("Bearer {{ $node.Login.json.token }}", CTX) == "Bearer abc"

def test_interpolate_walks_dict_and_list():
    out = expr.interpolate({"url": "/p/{{ $json.id }}", "xs": ["{{ $vars.rate }}"]}, CTX)
    assert out == {"url": "/p/7", "xs": [0.9]}
