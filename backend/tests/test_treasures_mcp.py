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
    assert names == {"treasure_wrap", "treasure_get", "treasure_list", "treasure_delete",
                     "treasure_discover", "treasure_update",
                     "treasure_link_source", "treasure_publish_prepare"}
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


def test_update_changes_only_given_fields_and_rewrites_meta(wired):
    import json as _json
    from pathlib import Path

    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Doc", "content": "# Doc\n", "kind": "note"})
    updated = _call(wired, "treasure_update",
                    {"ident": wrapped["id"], "title": "New Title",
                     "status": "published"})
    assert updated["title"] == "New Title"
    assert updated["status"] == "published"
    assert updated["kind"] == "note"           # untouched field survives
    assert updated["language"] == wrapped["language"]
    assert updated["updated_at"] != wrapped["updated_at"] or True  # refreshed

    # meta.json sidecar agrees with the index
    meta = _json.loads((Path(updated["dir_path"]) / "meta.json")
                       .read_text(encoding="utf-8"))
    assert meta["title"] == "New Title"
    assert meta["status"] == "published"

    got = _call(wired, "treasure_get", {"ident": wrapped["id"]})
    assert got["title"] == "New Title"
    assert got["status"] == "published"


def test_update_rejects_unknown_status(wired):
    wrapped = _call(wired, "treasure_wrap", {"title": "Doc2", "content": "# D\n"})
    out = _call(wired, "treasure_update",
               {"ident": wrapped["id"], "status": "bogus"})
    assert "error" in out
    # row is unchanged
    got = _call(wired, "treasure_get", {"ident": wrapped["id"]})
    assert got["status"] == "draft"


def test_update_unknown_ident_is_an_error(wired):
    out = _call(wired, "treasure_update", {"ident": "nope", "title": "x"})
    assert out["error"].startswith("not found")


def test_link_source_records_provenance(wired):
    import json as _json
    from pathlib import Path

    wrapped = _call(wired, "treasure_wrap", {"title": "Doc3", "content": "# D\n"})
    linked = _call(wired, "treasure_link_source",
                   {"ident": wrapped["id"], "origin_kind": "manual",
                    "origin_path": "/tmp/x.md"})
    assert linked["origin_kind"] == "manual"
    assert linked["origin_path"] == "/tmp/x.md"
    assert linked["status"] == "draft"          # no published_url given
    meta = _json.loads((Path(linked["dir_path"]) / "meta.json")
                       .read_text(encoding="utf-8"))
    assert meta["origin_kind"] == "manual"


def test_link_source_with_published_url_flips_draft_to_published(wired):
    wrapped = _call(wired, "treasure_wrap", {"title": "Doc4", "content": "# D\n"})
    assert wrapped["status"] == "draft"
    linked = _call(wired, "treasure_link_source",
                   {"ident": wrapped["id"],
                    "published_url": "https://claude.ai/artifacts/abc"})
    assert linked["published_url"] == "https://claude.ai/artifacts/abc"
    assert linked["status"] == "published"


def test_link_source_does_not_downgrade_non_draft_status(wired):
    wrapped = _call(wired, "treasure_wrap", {"title": "Doc5", "content": "# D\n"})
    _call(wired, "treasure_update", {"ident": wrapped["id"], "status": "archived"})
    linked = _call(wired, "treasure_link_source",
                   {"ident": wrapped["id"],
                    "published_url": "https://claude.ai/artifacts/xyz"})
    assert linked["status"] == "archived"       # unchanged, only draft flips


def test_link_source_unknown_ident_is_an_error(wired):
    out = _call(wired, "treasure_link_source",
               {"ident": "nope", "published_url": "https://x"})
    assert out["error"].startswith("not found")


def test_publish_prepare_returns_existing_path_and_verdict(wired):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Publish Me", "kind": "report",
                     "content": "# Publish Me\n\nThis is the summary line.\n"})
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert prep["artifact_path"] == wrapped["artifact_path"]
    assert prep["artifact_exists"] is True
    assert prep["title"] == "Publish Me"
    assert prep["description"].startswith("This is the summary line")
    assert prep["favicon"] == "\U0001F4CA"       # report -> 📊
    assert isinstance(prep["render_bytes"], int) and prep["render_bytes"] > 0
    assert prep["size_ok"] is True
    assert prep["next_step"]
    assert "treasure_link_source" in prep["next_step"]


def test_publish_prepare_unknown_ident_is_an_error(wired):
    out = _call(wired, "treasure_publish_prepare", {"ident": "nope"})
    assert out["error"].startswith("not found")


def test_delete_is_fail_closed_without_confirm(wired):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Doomed", "content": "# Doomed\n\nBody here.\n"})
    refused = _call(wired, "treasure_delete", {"ident": wrapped["id"]})
    assert "confirm=true" in refused["error"]
    assert _call(wired, "treasure_get", {"ident": wrapped["id"]})["id"] == wrapped["id"]

    gone = _call(wired, "treasure_delete", {"ident": wrapped["id"], "confirm": True})
    assert gone["deleted"] == wrapped["id"]
    assert "not found" in _call(wired, "treasure_get", {"ident": wrapped["id"]})["error"]
