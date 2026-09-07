"""The wikilink graph, shaped for a table first and a drawing second.

At this size the store has no dense clusters — most nodes carry nought to three links —
so a force layout has nothing to separate and its node positions would move between
renders, which is exactly what a tool used daily must not do. Sorting by degree puts the
isolated nodes at the top of a plain table, which answers the same question faster.

Broken edges are kept, not dropped. An edge whose target does not exist is the finding;
removing it would make the graph look healthy and quietly turn its source into an
isolated node.
"""
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.memory import store


def build(memory_dir) -> dict:
    memories = store.load_all(Path(memory_dir))
    names = {m.name for m in memories}

    edges, missing = [], []
    indegree = {m.name: 0 for m in memories}
    outdegree = {m.name: 0 for m in memories}

    for m in memories:
        for target in m.links:
            live = target in names
            edges.append({"source": m.name, "target": target, "broken": not live})
            if live:
                outdegree[m.name] += 1
                indegree[target] += 1
            elif target not in missing:
                missing.append(target)

    nodes = [{"name": m.name,
              "type": m.type,
              "in": indegree[m.name],
              "out": outdegree[m.name],
              "description": m.description,
              "last_updated": datetime.fromtimestamp(
                  m.mtime, tz=timezone.utc).isoformat()}
             for m in memories]
    nodes.sort(key=lambda n: (n["in"] + n["out"], n["name"]))

    groups: dict[str, list[str]] = {}
    for m in memories:
        groups.setdefault(m.type or "untyped", []).append(m.name)

    return {"nodes": nodes, "edges": edges,
            "missing_targets": sorted(missing), "groups": groups}
