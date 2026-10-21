import json
from datetime import datetime, timedelta, timezone

from conftest import load

resource_context = load("resource_context")


def snapshot(now):
    return {
        "schema_version": 1,
        "available": True,
        "complete": True,
        "boot_id": "boot",
        "namespace": "pid:[200]",
        "taken_at": now.isoformat(),
        "processes": [],
        "mcp_servers": [],
    }


def context_of(prompt):
    prefix = resource_context.RESOURCE_CHAT_CONTEXT_PREFIX
    return json.loads(prompt[len(prefix):].split("\n\nOperator request:\n", 1)[0])


def test_snapshot_freshness_bounds_and_unknowns():
    now = datetime(2026, 10, 13, 3, 30, tzinfo=timezone.utc)
    fresh = snapshot(now)
    assert resource_context.resource_snapshot_fresh(fresh, now)
    assert resource_context.resource_snapshot_fresh(snapshot(now - timedelta(seconds=45)), now)
    assert not resource_context.resource_snapshot_fresh(snapshot(now - timedelta(seconds=46)), now)
    assert not resource_context.resource_snapshot_fresh(snapshot(now + timedelta(seconds=1)), now)
    for value in [None, {}, {**fresh, "error": "unavailable"}, {**fresh, "complete": False},
                  {**fresh, "taken_at": "invalid"}, {**fresh, "taken_at": "2026-10-13T03:30:00"},
                  {**fresh, "processes": None}, {**fresh, "namespace": 200},
                  {**fresh, "schema_version": 2}, {**fresh, "available": None},
                  {**fresh, "complete": None}]:
        assert not resource_context.resource_snapshot_fresh(value, now)


def test_context_whitelists_data_and_reports_sampling_limits():
    data = snapshot(datetime.now(timezone.utc))
    data["processes"] = [
        {"key": str(i), "pid": i + 100, "comm": "<script>untrusted</script>",
         "env": {"DO_NOT_INCLUDE": "hidden"}, "command_line": "hidden",
         "sockets": [{"inode": str(j), "protocol": "tcp", "state": "CLOSE_WAIT",
                      "remote_url": "hidden"} for j in range(12)]}
        for i in range(55)
    ]
    data["mcp_servers"] = [
        {"key": "mcp:" + str(i), "name": "server", "status": "connected",
         "config": {"hidden": "do not include"}, "token": "hidden"}
        for i in range(103)
    ]
    prompt = resource_context.build_resource_chat_prompt(data, "Kiểm tra giúp tôi")
    context = context_of(prompt)
    assert context["observed_process_count"] == 55
    assert context["included_process_count"] == 50
    assert context["omitted_scoped_processes"] == 5
    assert len(context["mcp_servers"]) == 100
    assert context["omitted_mcp_servers"] == 3
    assert len(context["processes"][0]["sockets"]) == 10
    assert context["processes"][0]["omitted_socket_observations"] == 2
    assert "hidden" not in prompt
    assert context["processes"][0]["comm"] == "<script>untrusted</script>"
    assert prompt.endswith("Operator request:\nKiểm tra giúp tôi")


def test_context_selection_is_explicit_and_missing_keys_are_visible():
    data = snapshot(datetime.now(timezone.utc))
    data["processes"] = [{"key": "a", "pid": 101}, {"key": "b", "pid": 102}]
    context = context_of(resource_context.build_resource_chat_prompt(data, "Check", ["b", "gone", "b"]))
    assert context["selected_keys"] == ["b", "gone"]
    assert context["missing_selected_keys"] == ["gone"]
    assert context["observed_process_count"] == 2
    assert context["included_process_count"] == 1
    assert context["processes"] == [{"key": "b", "pid": 102, "sockets": [], "omitted_socket_observations": 0}]


def test_unavailable_context_does_not_turn_missing_inventory_into_zero():
    context = context_of(resource_context.build_resource_chat_prompt({"processes": None, "mcp_servers": None}, "Check"))
    assert context["stale"]
    assert context["observed_process_count"] is None
    assert context["included_process_count"] == 0
