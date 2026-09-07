"""The link graph: degrees, groups, and the ghost nodes broken links point at."""
from pathlib import Path

import pytest

from flightdeck.memory import graph


def write(d: Path, name: str, type_: str, body: str):
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: \"desc for {name}\"\n"
        f"metadata: \n  type: {type_}\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    write(d, "hub", "reference", "no links out")
    write(d, "spoke1", "reference", "see [[hub]]")
    write(d, "spoke2", "project", "see [[hub]] and [[ghost]]")
    write(d, "lonely", "feedback", "nothing here")
    return d


def test_degrees_are_counted_in_both_directions(store_dir):
    nodes = {n["name"]: n for n in graph.build(store_dir)["nodes"]}
    assert (nodes["hub"]["in"], nodes["hub"]["out"]) == (2, 0)
    assert (nodes["spoke2"]["in"], nodes["spoke2"]["out"]) == (0, 1)


def test_nodes_are_sorted_fewest_links_first(store_dir):
    assert graph.build(store_dir)["nodes"][0]["name"] == "lonely"


def test_a_link_to_a_missing_name_is_marked_broken_and_listed(store_dir):
    out = graph.build(store_dir)
    broken = [e for e in out["edges"] if e["broken"]]
    assert [(e["source"], e["target"]) for e in broken] == [("spoke2", "ghost")]
    assert out["missing_targets"] == ["ghost"]


def test_a_broken_edge_does_not_add_out_degree(store_dir):
    nodes = {n["name"]: n for n in graph.build(store_dir)["nodes"]}
    assert nodes["spoke2"]["out"] == 1  # only the live edge to hub


def test_groups_hold_every_node_exactly_once(store_dir):
    out = graph.build(store_dir)
    flat = [n for names in out["groups"].values() for n in names]
    assert sorted(flat) == ["hub", "lonely", "spoke1", "spoke2"]
