"""The git layer under the memory store.

The property that matters is that git leaves NO trace inside the directory the harness
scans — no .git, no .md, nothing. Everything else here is ordinary plumbing.
"""
import subprocess
from pathlib import Path

import pytest

from flightdeck.memory import vcs


@pytest.fixture()
def repo(tmp_path):
    mem = tmp_path / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text("first\n", encoding="utf-8")
    vcs.init(mem)
    return mem


def test_the_git_dir_is_a_sibling_of_the_memory_dir(repo):
    assert vcs.git_dir(repo) == repo.parent / "memory.git"
    assert vcs.git_dir(repo).is_dir()


def test_git_leaves_nothing_inside_the_scanned_directory(repo):
    vcs.commit(repo, "first commit")
    assert [p.name for p in repo.iterdir()] == ["alpha.md"]


def test_commit_reports_the_files_it_captured(repo):
    out = vcs.commit(repo, "first commit")
    assert out["committed"] is True and out["files"] == ["alpha.md"]


def test_a_second_commit_with_no_change_is_a_no_op(repo):
    vcs.commit(repo, "first commit")
    out = vcs.commit(repo, "nothing changed")
    assert out["committed"] is False and out["files"] == []


def test_log_and_show_recover_an_earlier_version(repo):
    vcs.commit(repo, "first commit")
    (repo / "alpha.md").write_text("second\n", encoding="utf-8")
    vcs.commit(repo, "second commit")

    entries = vcs.log(repo, "alpha.md")
    assert [e["message"] for e in entries] == ["second commit", "first commit"]
    assert vcs.show(repo, entries[1]["sha"], "alpha.md") == "first\n"


def test_is_initialised_is_false_before_init(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    assert vcs.is_initialised(mem) is False
