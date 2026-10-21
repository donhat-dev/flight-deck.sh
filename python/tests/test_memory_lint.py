"""The eight lint rules, each proven on a fixture built to trip exactly it."""
from pathlib import Path

import pytest

from flightdeck.memory import lint


def write(d: Path, name: str, *, description="A sufficiently long description here",
          type_="reference", origin=None, source=None, body="plain body"):
    front = ["---", f"name: {name}", f'description: "{description}"', "metadata: "]
    if type_:
        front.append(f"  type: {type_}")
    if origin:
        front.append(f"  originSessionId: {origin}")
    if source:
        front.append(f"  source: {source}")
    front.append("---")
    (d / f"{name}.md").write_text("\n".join(front) + "\n\n" + body + "\n",
                                  encoding="utf-8")


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d


def test_reference_without_source_is_flagged(store_dir):
    write(store_dir, "alpha", source=None)
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    out = lint.run(store_dir)
    assert out["counts"]["no_source"] == 1


def test_a_feedback_memory_without_source_is_not_flagged(store_dir):
    write(store_dir, "alpha", type_="feedback", source=None)
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"].get("no_source", 0) == 0


def test_dead_origin_session_is_flagged(store_dir, tmp_path):
    write(store_dir, "alpha", source="docs/x.md@abc123",
          origin="99999999-9999-9999-9999-999999999999")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    out = lint.run(store_dir, transcript_dir=transcripts)
    assert out["counts"]["dead_origin"] == 1


def test_a_live_origin_session_is_not_flagged(store_dir, tmp_path):
    sid = "11111111-1111-1111-1111-111111111111"
    write(store_dir, "alpha", source="docs/x.md@abc123", origin=sid)
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    (transcripts / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
    out = lint.run(store_dir, transcript_dir=transcripts)
    assert out["counts"].get("dead_origin", 0) == 0


def test_a_short_description_is_flagged_as_weak(store_dir):
    write(store_dir, "alpha", description="short", source="docs/x.md@abc")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["weak_description"] == 1


def test_a_file_absent_from_the_index_is_flagged(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc")
    (store_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    out = lint.run(store_dir)
    assert out["counts"]["not_in_index"] == 1


def test_a_broken_link_is_flagged_with_a_close_name_suggestion(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="see [[beta-fact-here]]")
    write(store_dir, "beta-fact-hero", source="docs/y.md@abc", body="see [[alpha]]")
    (store_dir / "MEMORY.md").write_text(
        "- [A](alpha.md) — hook\n- [B](beta-fact-hero.md) — hook\n", encoding="utf-8")
    out = lint.run(store_dir)
    broken = [f for f in out["findings"] if f["kind"] == "broken_link"]
    assert len(broken) == 1
    assert broken[0]["suggestion"] == "beta-fact-hero"
    assert broken[0]["action"] == "rename_link"


def test_a_broken_link_with_no_close_name_offers_no_action(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="see [[zzzzzzzzzzzz]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    broken = [f for f in lint.run(store_dir)["findings"] if f["kind"] == "broken_link"]
    assert broken[0]["suggestion"] is None and broken[0]["action"] is None


def test_an_unlinked_memory_is_flagged(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="no links at all")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["not_linked"] == 1


def test_an_index_pointer_to_a_missing_file_is_flagged(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc")
    (store_dir / "MEMORY.md").write_text(
        "- [A](alpha.md) — hook\n- [Gone](gone.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["missing_file"] == 1


def test_an_alias_link_is_its_own_kind_and_does_not_count_as_broken(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc",
          body="points at [[some future thing|label]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    out = lint.run(store_dir)
    assert out["counts"]["future_target"] == 1
    assert out["counts"].get("broken_link", 0) == 0


def test_an_alias_link_is_not_a_connection(store_dir):
    """It earns its own finding, but it does not rescue the file from isolation: the
    graph draws edges from resolvable links only, and the two views must agree."""
    write(store_dir, "alpha", source="docs/x.md@abc",
          body="points at [[some future thing|label]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    counts = lint.run(store_dir)["counts"]
    assert counts["future_target"] == 1
    assert counts["not_linked"] == 1


def test_a_broken_link_is_not_a_connection_either(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="see [[nowhere-at-all]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    counts = lint.run(store_dir)["counts"]
    assert counts["broken_link"] == 1
    assert counts["not_linked"] == 1


def test_findings_are_ordered_heaviest_kind_first(store_dir):
    write(store_dir, "alpha", source=None, body="no links")
    (store_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    order = [f["kind"] for f in lint.run(store_dir)["findings"]]
    assert order.index("no_source") < order.index("not_in_index")
    assert order.index("not_in_index") < order.index("not_linked")


def test_two_memories_with_near_identical_summaries_are_both_weak(store_dir):
    write(store_dir, "alpha", description="The compose file mounts from the worktree",
          source="docs/x.md@abc", body="see [[beta]]")
    write(store_dir, "beta", description="The compose files mount from the worktree",
          source="docs/y.md@abc", body="see [[alpha]]")
    (store_dir / "MEMORY.md").write_text(
        "- [A](alpha.md) — hook\n- [B](beta.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["weak_description"] >= 1
