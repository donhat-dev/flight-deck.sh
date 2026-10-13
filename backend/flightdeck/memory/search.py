"""Full-text search over the memory store, with one-hop neighbour expansion.

Recall in this harness runs on a single cue: the one-line description in the index. When
that line is written badly there is no second route to the file, and most of a store can
sit unread for that reason alone. This tool is the second route.

Neighbour expansion is the other half. Human recall spreads from a cue to whatever is
associated with it; the wikilinks in this store already encode those associations but
nothing traverses them automatically. Returning each hit's neighbours — their summary
lines only, so it stays cheap — makes the links do retrieval work instead of decoration.

Plain substring matching, deliberately. The store is small enough that scanning it costs
nothing, and a derived index (SQLite FTS, embeddings) can be added later without moving
the storage contract: markdown stays the original, any index stays disposable.
"""
import re
from pathlib import Path

from flightdeck.memory import store

SNIPPET_RADIUS = 60


def _snippet(text: str, needle: str) -> str:
    lowered = text.lower()
    at = lowered.find(needle)
    if at < 0:
        return ""
    line_start = text.rfind("\n", 0, at) + 1
    line_end = text.find("\n", at)
    line = text[line_start:line_end if line_end >= 0 else len(text)].strip()
    if len(line) <= SNIPPET_RADIUS * 2:
        return line
    rel = at - line_start
    lo = max(0, rel - SNIPPET_RADIUS)
    return ("…" if lo else "") + line[lo:rel + SNIPPET_RADIUS].strip() + "…"


def run(memory_dir, query: str, limit: int = 10, neighbours: bool = True) -> dict:
    needle = (query or "").strip().lower()
    if not needle:
        return {"error": "empty query: give at least one search term"}

    memories = store.load_all(Path(memory_dir))
    by_name = {m.name: m for m in memories}
    results, total = [], 0

    for m in memories:
        in_desc = m.description.lower().count(needle)
        in_body = m.body.lower().count(needle)
        if not (in_desc or in_body):
            continue
        total += in_desc + in_body
        results.append({
            "name": m.name,
            "description": m.description,
            "type": m.type,
            "snippet": _snippet(m.body, needle) or _snippet(m.description, needle),
            "hits": in_desc + in_body,
            "in_index_only": in_body == 0,
            "neighbours": [
                {"name": t, "description": by_name[t].description}
                for t in m.links if t in by_name
            ] if neighbours else [],
        })

    results.sort(key=lambda r: (-r["hits"], r["name"]))
    return {"query": query, "matches": total, "in_memories": len(results),
            "results": results[:max(1, int(limit))]}
