"""memory_history: how one memory changed, read out of the sidecar git repository."""
from pathlib import Path

import pytest

from flightdeck.memory import history, vcs


@pytest.fixture()
def repo(tmp_path):
    mem = tmp_path / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"v1\"\nmetadata: \n  type: reference\n---\n\nfirst\n",
        encoding="utf-8")
    vcs.init(mem)
    vcs.commit(mem, "add alpha")
    (mem / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"v2\"\nmetadata: \n  type: reference\n---\n\nsecond\n",
        encoding="utf-8")
    vcs.commit(mem, "correct alpha")
    return mem


def test_lists_commits_newest_first(repo):
    out = history.run(repo, "alpha")
    assert [c["message"] for c in out["commits"]] == ["correct alpha", "add alpha"]


def test_reads_the_file_back_at_an_earlier_commit(repo):
    out = history.run(repo, "alpha")
    older = out["commits"][1]["sha"]
    assert "first" in history.run(repo, "alpha", at=older)["content"]


def test_an_unknown_memory_name_is_an_error(repo):
    assert "error" in history.run(repo, "nope")


def test_a_store_with_no_repository_says_so(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "alpha.md").write_text("---\nname: alpha\n---\n\nx\n", encoding="utf-8")
    out = history.run(mem, "alpha")
    assert "error" in out and "memory_history --init" in out["error"]
