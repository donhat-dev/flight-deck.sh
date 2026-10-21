"""The memory store reader: locating the directory, and parsing one file's shape.

Fixtures are written to tmp_path rather than read from the real store, because the
real store changes under us and a test that asserts "3 broken links" would break on
the next memory anyone saves.
"""
from pathlib import Path

import pytest

from flightdeck.memory import paths, store


def test_memory_dir_mirrors_the_harness_path_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    got = paths.memory_dir("/home/someone/Documents/Projects")
    assert got == tmp_path / "projects" / "-home-someone-Documents-Projects" / "memory"


def test_transcript_dir_is_the_memory_dir_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert paths.transcript_dir("/a/b") == paths.memory_dir("/a/b").parent


@pytest.fixture()
def sample(tmp_path):
    """Two memories: one well formed with a link, one carrying an alias link."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "alpha.md").write_text(
        "---\n"
        "name: alpha\n"
        'description: "The alpha fact"\n'
        "metadata: \n"
        "  node_type: memory\n"
        "  type: reference\n"
        "  originSessionId: 11111111-2222-3333-4444-555555555555\n"
        "---\n\n"
        "Body text linking to [[beta]] and again to [[beta]].\n",
        encoding="utf-8")
    (d / "beta.md").write_text(
        "---\n"
        "name: beta\n"
        "description: The beta fact\n"
        "metadata: \n"
        "  type: project\n"
        "---\n\n"
        "Points at [[gamma project|gamma]] which does not exist.\n",
        encoding="utf-8")
    (d / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [Alpha](alpha.md) — the alpha hook\n",
        encoding="utf-8")
    return d


def test_load_all_parses_frontmatter_and_links(sample):
    mems = {m.name: m for m in store.load_all(sample)}
    assert set(mems) == {"alpha", "beta"}          # MEMORY.md is not a memory
    a = mems["alpha"]
    assert a.description == "The alpha fact"        # quotes stripped
    assert a.type == "reference"
    assert a.origin_session == "11111111-2222-3333-4444-555555555555"
    assert a.links == ["beta"]                      # deduped, order preserved
    assert a.alias_links == []


def test_alias_links_are_kept_apart_from_plain_links(sample):
    beta = next(m for m in store.load_all(sample) if m.name == "beta")
    assert beta.links == []
    assert beta.alias_links == ["gamma project"]


def test_missing_frontmatter_fields_are_none_not_a_crash(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "bare.md").write_text("no frontmatter at all\n", encoding="utf-8")
    m = store.load_all(d)[0]
    assert m.name == "bare" and m.type is None and m.description == ""


def test_load_index_reads_title_file_and_hook(sample):
    assert store.load_index(sample) == [("Alpha", "alpha.md", "the alpha hook")]


def test_load_all_on_a_missing_directory_returns_empty(tmp_path):
    assert store.load_all(tmp_path / "nope") == []
