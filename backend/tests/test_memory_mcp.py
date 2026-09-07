"""The memory domain as it appears on the agent surface.

The last test is the negative one the workspace convention requires: these four tools
are read-only, and that has to be demonstrated by running them against a real store and
showing the bytes did not move — not asserted from reading the code.
"""
import hashlib
from pathlib import Path

import pytest

from flightdeck.agentsurface import registry
from flightdeck.memory import mcp_server


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"A long enough description here\"\n"
        "metadata: \n  type: project\n---\n\nbody with [[beta]]\n", encoding="utf-8")
    (d / "beta.md").write_text(
        "---\nname: beta\ndescription: \"Another summary, wholly unalike\"\n"
        "metadata: \n  type: project\n---\n\nplain body\n", encoding="utf-8")
    (d / "MEMORY.md").write_text(
        "- [Alpha](alpha.md) — hook\n- [Beta](beta.md) — hook\n", encoding="utf-8")
    return d


def test_the_domain_is_registered_and_every_name_wears_its_prefix():
    tools = registry.merged()
    assert {"memory_lint", "memory_search", "memory_graph",
            "memory_history"} <= set(tools)
    assert all(n.startswith("memory_") for n in mcp_server.TOOLS)


def test_every_tool_has_a_description_and_a_schema():
    for name, (fn, description, props, required) in mcp_server.TOOLS.items():
        assert callable(fn), name
        assert len(description) > 40, name
        assert isinstance(props, dict) and isinstance(required, list), name


def test_lint_dispatches_through_the_registry(store_dir):
    out = registry.dispatch("memory_lint", {"memory_dir": str(store_dir)})
    assert out["total_memories"] == 2
    assert "counts" in out and "findings" in out


def test_search_dispatches_through_the_registry(store_dir):
    out = registry.dispatch("memory_search",
                            {"memory_dir": str(store_dir), "query": "body"})
    assert out["in_memories"] == 2


def test_an_unknown_memory_tool_is_reported_as_data(store_dir):
    assert "error" in registry.dispatch("memory_delete", {})


def test_a_missing_store_is_an_error_not_a_crash(tmp_path):
    out = registry.dispatch("memory_graph", {"memory_dir": str(tmp_path / "nope")})
    assert "error" in out


def test_the_four_tools_do_not_write_to_the_store(store_dir):
    """The live negative test. Run all four, then prove nothing on disk moved."""
    def fingerprint():
        return sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                      for p in Path(store_dir).iterdir())

    before = fingerprint()
    registry.dispatch("memory_lint", {"memory_dir": str(store_dir)})
    registry.dispatch("memory_search", {"memory_dir": str(store_dir), "query": "body"})
    registry.dispatch("memory_graph", {"memory_dir": str(store_dir)})
    registry.dispatch("memory_history", {"memory_dir": str(store_dir), "name": "alpha"})
    assert fingerprint() == before
