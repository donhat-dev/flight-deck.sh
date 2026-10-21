import os
import sqlite3

import pytest

from flightdeck.hub import store, credentials

def test_flow_roundtrip(tmp_path):
    f = {"name": "My Flow", "nodes": [], "connections": []}
    saved = store.save_flow(str(tmp_path), f)
    assert saved["id"]
    assert store.load_flow(str(tmp_path), saved["id"])["name"] == "My Flow"
    assert any(x["id"] == saved["id"] for x in store.list_flows(str(tmp_path)))
    store.delete_flow(str(tmp_path), saved["id"])
    assert store.list_flows(str(tmp_path)) == []

def test_credentials_hide_secret_in_list():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    credentials.init(conn)
    c = credentials.upsert(conn, {"name": "Odoo local", "kind": "odoo",
                                  "data": {"base": "http://x", "db": "d",
                                           "user": "a", "secret": "p"}})
    listed = credentials.list(conn)
    assert listed[0]["name"] == "Odoo local"
    assert "data" not in listed[0] and "secret" not in str(listed[0])
    assert credentials.get(conn, c["id"])["data"]["secret"] == "p"


@pytest.mark.parametrize(
    "bad", ["../evil", "../../etc/passwd", "/etc/cron.d/evil", "a/b", ".hidden"]
)
def test_path_traversal_id_rejected(tmp_path, bad):
    flows_dir = str(tmp_path)

    with pytest.raises(ValueError):
        store.save_flow(flows_dir, {"id": bad, "name": "evil", "nodes": [], "connections": []})
    with pytest.raises(ValueError):
        store.load_flow(flows_dir, bad)
    with pytest.raises(ValueError):
        store.delete_flow(flows_dir, bad)

    # Resolve where the traversal/absolute id would have landed and assert
    # nothing was ever written there.
    if os.path.isabs(bad):
        escaped_target = bad + ".json"
    else:
        escaped_target = os.path.normpath(os.path.join(flows_dir, bad + ".json"))
    assert not os.path.exists(escaped_target)

    # And nothing leaked into flows_dir itself either.
    assert not os.path.isdir(flows_dir) or os.listdir(flows_dir) == []


def test_load_flow_missing_returns_none(tmp_path):
    assert store.load_flow(str(tmp_path), "does-not-exist") is None


def test_credentials_upsert_updates_existing():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    credentials.init(conn)
    c = credentials.upsert(conn, {"name": "Odoo local", "kind": "odoo",
                                  "data": {"base": "http://x", "db": "d",
                                           "user": "a", "secret": "p"}})
    credentials.upsert(conn, {"id": c["id"], "name": "Odoo renamed", "kind": "odoo",
                               "data": {"base": "http://y", "db": "d",
                                        "user": "a", "secret": "q"}})

    updated = credentials.get(conn, c["id"])
    assert updated["name"] == "Odoo renamed"
    assert updated["data"]["base"] == "http://y"
    assert updated["data"]["secret"] == "q"

    listed = credentials.list(conn)
    assert len(listed) == 1
    assert listed[0]["name"] == "Odoo renamed"
    assert "data" not in listed[0] and "secret" not in str(listed[0])
