"""Counting how often each memory was actually read, out of the session transcripts."""
import json
from pathlib import Path

import pytest

from flightdeck.memory import usage


def _turn(tool: str, path: str) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": tool, "input": {"file_path": path}}]}})


@pytest.fixture()
def corpus(tmp_path):
    proj = tmp_path / "-proj"
    mem = proj / "memory"
    mem.mkdir(parents=True)
    for n in ("alpha.md", "beta.md", "unread.md"):
        (mem / n).write_text("x\n", encoding="utf-8")
    (proj / "s1.jsonl").write_text(
        "\n".join([_turn("Read", str(mem / "alpha.md")),
                   _turn("Read", str(mem / "alpha.md")),
                   _turn("Read", str(mem / "beta.md")),
                   _turn("Read", "/etc/passwd"),
                   "not json at all"]) + "\n", encoding="utf-8")
    return proj, mem


def test_counts_reads_per_memory_file(corpus):
    proj, mem = corpus
    assert usage.read_counts(proj, mem) == {"alpha.md": 2, "beta.md": 1}


def test_files_never_read_are_absent_not_zero(corpus):
    proj, mem = corpus
    assert "unread.md" not in usage.read_counts(proj, mem)


def test_grep_over_the_memory_dir_counts_for_every_file_it_covers(tmp_path):
    proj = tmp_path / "-proj"
    mem = proj / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text("x\n", encoding="utf-8")
    (proj / "s.jsonl").write_text(json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "g", "name": "Grep",
             "input": {"pattern": "x", "path": str(mem)}}]}}) + "\n", encoding="utf-8")
    assert usage.read_counts(proj, mem) == {"alpha.md": 1}


def test_a_missing_transcript_dir_is_empty_not_an_error(tmp_path):
    assert usage.read_counts(tmp_path / "nope", tmp_path) == {}
