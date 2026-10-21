import json

from flightdeck import db
from flightdeck.treasures import discover, filestore, store

MD_BODY = "# Báo cáo Helpdesk\n\n" + ("Nội dung tiếng Việt đầy đủ. " * 30)
HTML_BODY = "<h1>Report</h1>" + ("<p>Long enough body.</p>" * 30)


def _jsonl(path, session_id, blocks):
    """One assistant line per block set, in the shape Claude Code writes."""
    with open(path, "w", encoding="utf-8") as fh:
        for content in blocks:
            fh.write(json.dumps({
                "type": "assistant", "uuid": f"u-{session_id}",
                "sessionId": session_id, "timestamp": "2026-07-20T10:00:00Z",
                "cwd": "/home/x/proj",
                "message": {"content": content},
            }) + "\n")


def _tree(tmp_path):
    proj = tmp_path / "projects" / "-home-x-proj"
    proj.mkdir(parents=True)
    _jsonl(proj / "sess-a.jsonl", "sess-a", [[
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/docs/bao-cao.md", "content": MD_BODY}},
    ], [
        {"type": "text", "text": "some prose, not a document"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/notes.txt", "content": MD_BODY}},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/tiny.md", "content": "# hi"}},
    ]])
    _jsonl(proj / "sess-b.jsonl", "sess-b", [[
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/report.html", "content": HTML_BODY}},
    ]])
    return tmp_path / "projects"


def test_scan_finds_only_document_writes(tmp_path):
    out = discover.scan(str(_tree(tmp_path)))
    paths = sorted(c["file_path"] for c in out["candidates"])
    assert paths == ["/home/x/proj/docs/bao-cao.md", "/home/x/proj/report.html"]
    assert out["scanned"] == 2                      # two jsonl files
    assert out["skipped"]["not_a_document"] >= 2    # notes.txt + Bash
    assert out["skipped"]["too_small"] == 1         # tiny.md
    assert out["bounds"]["min_bytes"] > 0           # bounds are reported


def test_candidate_metadata_is_useful(tmp_path):
    out = discover.scan(str(_tree(tmp_path)))
    md = next(c for c in out["candidates"] if c["file_path"].endswith(".md"))
    assert md["title"] == "Báo cáo Helpdesk"        # from the first heading
    assert md["language"] == "vi"                    # Vietnamese glyphs present
    assert md["source_format"] == "markdown"
    assert md["origin_id"] == "sess-a"               # closes the origin_id gap
    assert md["origin_path"].endswith("sess-a.jsonl")
    assert md["authored_at"] == "2026-07-20T10:00:00Z"
    assert md["checksum"] == filestore.checksum(MD_BODY)
    assert md["bytes"] == len(MD_BODY.encode("utf-8"))

    html = next(c for c in out["candidates"] if c["file_path"].endswith(".html"))
    assert html["source_format"] == "html"
    assert html["language"] == "en"


def test_run_marks_already_imported_and_can_import(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    projects = str(_tree(tmp_path))

    first = discover.run(conn, projects)
    assert first["imported"] == 0
    assert all(c["already_imported"] is False for c in first["candidates"])

    imported = discover.run(conn, projects, do_import=True)
    assert imported["imported"] == 2
    rows = store.list_rows(conn)
    assert len(rows) == 2
    assert {r["origin_kind"] for r in rows} == {"claude_session"}
    assert {r["origin_id"] for r in rows} == {"sess-a", "sess-b"}

    # idempotent: a second import adds nothing, and candidates are flagged
    again = discover.run(conn, projects, do_import=True)
    assert again["imported"] == 0
    assert all(c["already_imported"] for c in again["candidates"])
    assert len(store.list_rows(conn)) == 2


def test_scan_skips_the_filestore_itself(monkeypatch, tmp_path):
    """Discovering our own wrapped output would loop artifacts back in."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    proj = tmp_path / "projects" / "-p"
    proj.mkdir(parents=True)
    inside = str(filestore.root() / "x-123" / "v1" / "artifact.html")
    _jsonl(proj / "s.jsonl", "s", [[
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": inside, "content": HTML_BODY}}]])
    out = discover.scan(str(tmp_path / "projects"))
    assert out["candidates"] == []
    assert out["skipped"]["in_filestore"] == 1


def test_one_unrenderable_document_does_not_abort_the_batch(monkeypatch, tmp_path):
    """A partial import that reports what it dropped beats one that stops dead."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    projects = str(_tree(tmp_path))

    real_wrap = discover.service.wrap
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pandoc failed: synthetic")
        return real_wrap(*args, **kwargs)

    monkeypatch.setattr(discover.service, "wrap", flaky)
    out = discover.run(conn, projects, do_import=True)
    assert out["imported"] == 1                     # the second one still landed
    assert len(out["failed"]) == 1
    assert "synthetic" in out["failed"][0]["error"]
    assert len(store.list_rows(conn)) == 1
