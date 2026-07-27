"""Focused tests for treasure_publish_prepare beyond the round-trip already
covered in test_treasures_mcp.py: favicon-by-kind mapping, the description
fallback to the title, and the 16 MiB size_ok boundary."""
import json

import pytest

from flightdeck.treasures import mcp_server


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    mcp_server.configure({"db_path": str(tmp_path / "t.db"),
                          "database_url": None})
    return mcp_server


def _call(server, name, args):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return json.loads(resp["result"]["content"][0]["text"])


@pytest.mark.parametrize("kind,expected", [
    ("report", "\U0001F4CA"),       # 📊
    ("spec-review", "\U0001F9ED"),  # 🧭
    ("note", "\U0001F4DD"),         # 📝
    ("dataflow", "\U0001F500"),     # 🔀
    ("deck", "\U0001F39E"),         # 🎞
    ("something-else", "\U0001F48E"),  # 💎 default
])
def test_favicon_suggestion_by_kind(wired, kind, expected):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": f"{kind} doc", "kind": kind,
                     "content": f"# {kind} doc\n\nBody line.\n"})
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert prep["favicon"] == expected


def test_description_falls_back_to_title_when_no_body_line(wired):
    # only a heading line, no other content
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Heading Only", "content": "# Heading Only\n"})
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert prep["description"] == "Heading Only"


def test_description_is_trimmed_to_about_160_chars(wired):
    long_line = "x" * 250
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Long", "content": f"# Long\n\n{long_line}\n"})
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert len(prep["description"]) <= 161  # ~160 + ellipsis
    assert prep["description"].endswith("...")


def test_size_ok_false_over_the_16mib_cap(wired, monkeypatch):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Big", "content": "# Big\n\nBody.\n"})
    # Force the on-disk artifact past the 16 MiB claude.ai cap without
    # re-rendering a real oversized document.
    from pathlib import Path
    art_path = Path(wrapped["artifact_path"])
    art_path.write_text("x" * (16 * 1024 * 1024 + 1), encoding="utf-8")
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert prep["size_ok"] is False
    assert prep["render_bytes"] > 16 * 1024 * 1024


def test_publish_prepare_flags_missing_artifact_file(wired):
    wrapped = _call(wired, "treasure_wrap", {"title": "Gone", "content": "# Gone\n"})
    from pathlib import Path
    Path(wrapped["artifact_path"]).unlink()
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert prep["artifact_exists"] is False
