"""Full-text search over the store, plus one-hop neighbour expansion."""
from pathlib import Path

import pytest

from flightdeck.memory import search


def write(d: Path, name: str, description: str, body: str):
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: \"{description}\"\n"
        f"metadata: \n  type: reference\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    write(d, "worktree-source", "Both instances mount from the worktree",
          "The compose file mounts a worktree. See [[docker-setup]].")
    write(d, "docker-setup", "How to run the stack locally",
          "docker compose up. Nothing about trees here.")
    write(d, "unrelated", "Something else entirely", "no match in this one")
    return d


def test_finds_matches_in_the_body(store_dir):
    out = search.run(store_dir, "worktree")
    assert [r["name"] for r in out["results"]] == ["worktree-source"]
    assert out["in_memories"] == 1


def test_search_is_case_insensitive_and_counts_hits(store_dir):
    out = search.run(store_dir, "WORKTREE")
    assert out["results"][0]["hits"] == 2  # description + body


def test_the_snippet_carries_the_matched_line(store_dir):
    out = search.run(store_dir, "compose")
    assert "docker compose up" in out["results"][0]["snippet"]


def test_neighbours_bring_back_the_linked_memory_summary(store_dir):
    out = search.run(store_dir, "worktree")
    assert out["results"][0]["neighbours"] == [
        {"name": "docker-setup", "description": "How to run the stack locally"}]


def test_neighbours_can_be_switched_off(store_dir):
    out = search.run(store_dir, "worktree", neighbours=False)
    assert out["results"][0]["neighbours"] == []


def test_a_body_only_match_is_marked_as_absent_from_the_summary(store_dir):
    out = search.run(store_dir, "compose")
    assert out["results"][0]["in_index_only"] is False


def test_an_empty_query_is_an_error_not_an_empty_result(store_dir):
    assert "error" in search.run(store_dir, "   ")


def test_no_match_returns_an_empty_result_list(store_dir):
    out = search.run(store_dir, "zzzznotpresent")
    assert out["results"] == [] and "error" not in out
