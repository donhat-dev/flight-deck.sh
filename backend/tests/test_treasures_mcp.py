import json

import pytest

from flightdeck.treasures import mcp_server


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Point the server at a scratch SQLite DB + filestore."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    mcp_server.configure({"db_path": str(tmp_path / "t.db"),
                          "database_url": None})
    return mcp_server


def _call(server, name, args):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return json.loads(resp["result"]["content"][0]["text"])


def test_initialize_and_tools_list(wired):
    init = wired.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "treasures"
    listed = wired.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"treasure_wrap", "treasure_get", "treasure_list",
                     "treasure_discover"}
    for tool in listed["result"]["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_notifications_get_no_response(wired):
    assert wired.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_wrap_get_list_round_trip(wired):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Báo cáo", "content": "# Báo cáo\n\nNội dung.\n",
                     "language": "vi", "origin_id": "sess-9"})
    assert wrapped["language"] == "vi"
    assert wrapped["version"] == 1
    assert wrapped["artifact_path"].endswith("v1/artifact.html")

    got = _call(wired, "treasure_get", {"ident": wrapped["id"],
                                        "include_source": True})
    assert got["source"].startswith("# Báo cáo")

    listed = _call(wired, "treasure_list", {"language": "vi"})
    assert [r["id"] for r in listed["treasures"]] == [wrapped["id"]]
    assert _call(wired, "treasure_list", {"language": "en"})["treasures"] == []


def test_tool_errors_come_back_as_data_not_crashes(wired):
    out = _call(wired, "treasure_get", {"ident": "does-not-exist"})
    assert out["error"].startswith("not found")
    unknown = _call(wired, "nope", {})
    assert "unknown tool" in unknown["error"]


def test_unknown_method_returns_jsonrpc_error(wired):
    resp = wired.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
    assert resp["error"]["code"] == -32601
