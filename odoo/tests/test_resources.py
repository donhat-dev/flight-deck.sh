import json
import os
import types
from datetime import datetime, timedelta, timezone

from conftest import load

check = load("resource_check")
context = load("resource_context")
policy = load("resource_policy")


def test_action_kinds_match_policy():
    assert check.ACTION_KINDS == ["stop_process", "disconnect_mcp", "reconnect_mcp"]
    assert check.valid_action_kind("stop_process")
    assert not check.valid_action_kind("kill")


def test_uuid_and_key_shapes():
    assert check.is_uuid("11111111-2222-3333-4444-555555555555")
    assert not check.is_uuid("../escape")
    assert not check.is_uuid("")
    assert check.valid_resource_key("bootid:pid:[4026531836]:10:12345")
    assert check.valid_resource_key("mcp:boot:pid:[1]:5:10:c2VydmVy")
    assert not check.valid_resource_key("../etc/passwd")
    assert not check.valid_resource_key("a b c")


def test_expected_process_validation():
    good = {
        "key": "b:pid:[1]:10:20", "pid": 10, "start_ticks": "20",
        "namespace": "pid:[1]", "uid": 1000, "exe_identity": "1:2",
    }
    assert check.validate_expected_process(good) is None
    for field in check.PROCESS_IDENTITY_FIELDS:
        bad = dict(good)
        del bad[field]
        assert check.validate_expected_process(bad), field
    bad = dict(good, pid="10")
    assert check.validate_expected_process(bad)
    bad = dict(good, pid=-1)
    assert check.validate_expected_process(bad)
    bad = dict(good, start_ticks=20)
    assert check.validate_expected_process(bad)
    bad = dict(good, start_ticks="20x")
    assert check.validate_expected_process(bad)
    bad = dict(good, start_ticks="020")
    assert check.validate_expected_process(bad)
    bad = dict(good, start_ticks=str(2**64))
    assert check.validate_expected_process(bad)
    row = dict(good, comm="claude", extra="dropped")
    assert check.expected_from_row(row) == good


def test_expiry_fails_closed():
    now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert check.parse_expiry("", now=now)[1]
    assert check.parse_expiry("not a date", now=now)[1]
    assert check.parse_expiry("2026-10-13 12:00", now=now)[1] == "expiry carries no timezone"
    past = (now - timedelta(seconds=1)).isoformat()
    due, err = check.parse_expiry(past, now=now)
    assert err == "expired" and due is not None
    future = (now + timedelta(seconds=30)).isoformat()
    due, err = check.parse_expiry(future, now=now)
    assert err is None and due.isoformat() == future


def _spool_connector(tmp_path):
    """A spool connector bound to a temp dir, runner stubbed by its fields."""
    import sys
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "flight_deck"))
    pkg = types.ModuleType("connectors_under_test")
    pkg.__path__ = [os.path.join(base, "connectors")]
    sys.modules["connectors_under_test"] = pkg
    import importlib
    spool = importlib.import_module("connectors_under_test.spool")
    runner = types.SimpleNamespace(spool_dir=str(tmp_path), kind="spool")
    return spool.SpoolConnector(runner)


def test_spool_snapshot_states(tmp_path):
    conn = _spool_connector(tmp_path)
    snap = conn.resource_snapshot()
    assert snap["available"] is False and snap["complete"] is False
    (tmp_path / "resources.json").write_text("{bad json")
    snap = conn.resource_snapshot()
    assert snap["complete"] is False and "JSON" in snap["error"]
    (tmp_path / "resources.json").write_text(json.dumps({"schema_version": 99}))
    snap = conn.resource_snapshot()
    assert snap["complete"] is False and "schema" in snap["error"]
    (tmp_path / "resources.json").write_text(json.dumps(["not", "a", "dict"]))
    snap = conn.resource_snapshot()
    assert snap["complete"] is False and snap["available"] is False
    payload = {"schema_version": 1, "available": True, "complete": True,
               "boot_id": "b", "namespace": "pid:[1]", "processes": []}
    (tmp_path / "resources.json").write_text(json.dumps(payload))
    snap = conn.resource_snapshot()
    assert snap["available"] is True and snap["boot_id"] == "b"


def test_spool_snapshot_size_bound(tmp_path):
    conn = _spool_connector(tmp_path)
    with open(tmp_path / "resources.json", "w") as f:
        f.write(" " * (8 * 1024 * 1024 + 1))
    snap = conn.resource_snapshot()
    assert snap["complete"] is False and "large" in snap["error"]


def test_context_freshness_rules():
    snap = {"schema_version": 1, "available": True, "complete": True,
            "boot_id": "b", "namespace": "n", "processes": [],
            "taken_at": datetime.now(timezone.utc).isoformat()}
    assert context.resource_snapshot_fresh(snap)
    assert not context.resource_snapshot_fresh({"schema_version": 1})
    old = dict(snap, taken_at=(datetime.now(timezone.utc) -
                             timedelta(seconds=policy.RESOURCE_SNAPSHOT_TTL_SECONDS + 5)).isoformat())
    assert not context.resource_snapshot_fresh(old)
    stale_error = dict(snap, error="x")
    assert not context.resource_snapshot_fresh(stale_error)


def test_chat_prompt_is_only_the_lead_context():
    snap = {"schema_version": 1, "available": True, "complete": True,
            "boot_id": "b", "namespace": "n", "processes": [], "mcp_servers": [],
            "taken_at": datetime.now(timezone.utc).isoformat()}
    prompt = context.build_resource_chat_prompt(snap, "what runs?")
    assert prompt.startswith(context.RESOURCE_CHAT_CONTEXT_PREFIX)
    assert prompt.endswith("Operator request:\nwhat runs?")
