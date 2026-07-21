"""Opt-in live smoke test: run a real one-node flow against a local service.

Skipped unless HUB_LIVE=1 (and the target service is up). Hits the Discount
Service health endpoint through the HTTP node + engine, over host.docker.internal
(the same reachability the presets use). This is the end-to-end proof that a
flow executes against a live internal service, kept out of the default suite so
CI / normal runs never depend on a running service.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HUB_LIVE") != "1",
    reason="live smoke; set HUB_LIVE=1 with the Discount Service up")


def test_discount_service_health_via_hub():
    from flightdeck.hub import engine
    from flightdeck.hub.nodes import load
    load.load_all()
    flow = {
        "id": "smoke",
        "name": "discount-health",
        "nodes": [
            {"id": "s", "type": "start", "label": "Start", "params": {"seed": {}}},
            {"id": "h", "type": "http", "label": "Health",
             "params": {"method": "GET",
                        "url": "http://host.docker.internal:8100/healthz"}},
        ],
        "connections": [{"from": ["s", 0], "to": ["h", 0]}],
    }
    res = engine.run_flow(flow)
    assert res["status"] == "ok"
    assert res["nodes"]["h"]["status"] == "ok"
    # success socket (0) received the response item
    assert res["nodes"]["h"]["outputsPerSocket"][0][0]["json"]["status"] == 200
