import json
from flightdeck import transcript


def _user(text, uuid="u1", ts="2026-07-02T00:00:00Z", meta=False):
    o = {"type": "user", "uuid": uuid, "sessionId": "s1", "cwd": "/p",
         "gitBranch": "master", "version": "2.0.0", "timestamp": ts,
         "message": {"role": "user", "content": text}}
    if meta:
        o["isMeta"] = True
    return json.dumps(o)


def _assistant(blocks, uuid="a1", ts="2026-07-02T00:01:00Z"):
    return json.dumps({
        "type": "assistant", "uuid": uuid, "sessionId": "s1", "cwd": "/p",
        "timestamp": ts, "message": {"role": "assistant", "content": blocks}})


def _write(tmp_path, *lines, sid="s1"):
    f = tmp_path / "proj" / f"{sid}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines) + "\n")
    return f


def test_find_session_file(tmp_path):
    _write(tmp_path, _user("hi"), sid="abc-123")
    assert transcript.find_session_file(str(tmp_path), "abc-123").endswith("abc-123.jsonl")
    assert transcript.find_session_file(str(tmp_path), "missing") is None


def test_find_session_file_rejects_traversal(tmp_path):
    assert transcript.find_session_file(str(tmp_path), "../../etc/passwd") is None
    assert transcript.find_session_file(str(tmp_path), "") is None


def test_string_and_block_content(tmp_path):
    tool_result = json.dumps({
        "type": "user", "uuid": "u2", "sessionId": "s1",
        "timestamp": "2026-07-02T00:02:00Z",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "file1\nfile2", "is_error": False}]}})
    f = _write(
        tmp_path,
        _user("hello there"),
        _assistant([
            {"type": "thinking", "thinking": "let me think"},
            {"type": "text", "text": "hi!"},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls"}},
        ]),
        tool_result,
    )
    data = transcript.build_transcript(str(f))
    assert data["turn_count"] == 3
    assert data["project"] == "/p"
    assert data["git_branch"] == "master"
    assert data["version"] == "2.0.0"

    t0, t1, t2 = data["turns"]
    assert t0["role"] == "user" and t0["blocks"][0]["text"] == "hello there"
    kinds = [b["type"] for b in t1["blocks"]]
    assert kinds == ["thinking", "text", "tool_use"]
    assert t1["blocks"][2]["name"] == "Bash"
    assert t1["blocks"][2]["input"]["command"] == "ls"
    assert t2["blocks"][0]["type"] == "tool_result"
    assert t2["blocks"][0]["content"] == "file1\nfile2"
    assert t2["blocks"][0]["is_error"] is False


def test_ai_title_and_summary_captured(tmp_path):
    f = _write(
        tmp_path,
        json.dumps({"type": "summary", "summary": "fallback"}),
        json.dumps({"type": "ai-title", "sessionId": "s1", "aiTitle": "Real Title"}),
        _user("hi"),
    )
    data = transcript.build_transcript(str(f))
    assert data["title"] == "Real Title"


def test_meta_turns_flagged(tmp_path):
    f = _write(
        tmp_path,
        _user("<system-reminder>injected</system-reminder>"),
        _user("real question", uuid="u2"),
        _user("explicit meta", uuid="u3", meta=True),
    )
    data = transcript.build_transcript(str(f))
    assert data["turns"][0]["is_meta"] is True
    assert data["turns"][1]["is_meta"] is False
    assert data["turns"][2]["is_meta"] is True


def test_empty_turns_dropped(tmp_path):
    f = _write(
        tmp_path,
        _user(""),                       # empty string -> no block
        _assistant([]),                  # empty content -> no block
        _user("kept"),
    )
    data = transcript.build_transcript(str(f))
    assert data["turn_count"] == 1
    assert data["turns"][0]["blocks"][0]["text"] == "kept"


def test_large_block_truncated(tmp_path):
    big = "x" * (transcript._MAX_BLOCK_CHARS + 5000)
    f = _write(tmp_path, _assistant([{"type": "text", "text": big}]))
    data = transcript.build_transcript(str(f))
    out = data["turns"][0]["blocks"][0]["text"]
    assert len(out) < len(big)
    assert "truncated" in out


def test_tool_result_list_content_flattened(tmp_path):
    f = _write(tmp_path, json.dumps({
        "type": "user", "uuid": "u1", "sessionId": "s1",
        "timestamp": "2026-07-02T00:00:00Z",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": "line a"},
                {"type": "image"},
            ]}]}}))
    data = transcript.build_transcript(str(f))
    assert data["turns"][0]["blocks"][0]["content"] == "line a\n[image]"


def test_pagination_window(tmp_path):
    lines = [_user(f"msg {i}", uuid=f"u{i}", ts=f"2026-07-02T00:0{i}:00Z")
             for i in range(5)]
    f = _write(tmp_path, *lines)
    data = transcript.build_transcript(str(f), offset=1, limit=2)
    assert data["turn_count"] == 5
    assert data["returned"] == 2
    assert data["offset"] == 1
    assert data["truncated"] is True
    assert [b["blocks"][0]["text"] for b in data["turns"]] == ["msg 1", "msg 2"]


def test_load_session_resolves_and_tags_id(tmp_path):
    _write(tmp_path, _user("hi"), sid="sess-xyz")
    data = transcript.load_session(str(tmp_path), "sess-xyz")
    assert data["session_id"] == "sess-xyz"
    assert data["turns"][0]["blocks"][0]["text"] == "hi"
    assert transcript.load_session(str(tmp_path), "nope") is None
