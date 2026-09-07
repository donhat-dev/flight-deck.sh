"""The `memory_*` agent surface: four read-only views over the auto-memory store.

None of these four writes to the store. That is the point of the split — the harness
owns the files and stamps their metadata on write; this domain only reads, derives, and
reports, so there is nothing here that can race the harness or corrupt what it wrote.
The one thing that does write is the sidecar git repository, and it lives outside the
directory the harness scans.

Together they answer four different questions, and each tool exists because a plain
`ls` of the store cannot answer its one:

- `memory_lint`   — is this store still trustworthy, and what needs doing
- `memory_search` — what did I write about X, when the summary line does not say so
- `memory_graph`  — how do these connect, and where is the graph torn
- `memory_history`— what did this memory say before, and why did it change
"""
from pathlib import Path

from flightdeck.memory import graph as graph_mod
from flightdeck.memory import history as history_mod
from flightdeck.memory import lint as lint_mod
from flightdeck.memory import paths
from flightdeck.memory import search as search_mod


def _dirs(memory_dir=None, cwd=None):
    if memory_dir:
        target = Path(memory_dir)
        return target, target.parent
    return paths.memory_dir(cwd), paths.transcript_dir(cwd)


def memory_lint(memory_dir=None, cwd=None, with_usage=True):
    store_dir, transcripts = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    if with_usage:
        return lint_mod.with_usage(store_dir, transcripts, cwd=cwd)
    return lint_mod.run(store_dir, transcript_dir=transcripts, cwd=cwd)


def memory_search(query, memory_dir=None, cwd=None, limit=10, neighbours=True):
    store_dir, _ = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    return search_mod.run(store_dir, query, limit=limit, neighbours=neighbours)


def memory_graph(memory_dir=None, cwd=None):
    store_dir, _ = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    return graph_mod.build(store_dir)


def memory_history(name=None, memory_dir=None, cwd=None, limit=20, at=None, init=False):
    store_dir, _ = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    if init:
        return history_mod.init(store_dir)
    if not name:
        return {"error": "give a memory name, or pass init=true to create the "
                         "version history for this store"}
    return history_mod.run(store_dir, name, limit=limit, at=at)


_DIR_PROP = {"type": "string",
             "description": "path to the memory store; omit to resolve it from cwd "
                            "the way the harness does"}
_CWD_PROP = {"type": "string",
             "description": "project directory to resolve the store from; defaults "
                            "to the current working directory"}

TOOLS = {
    "memory_lint": (
        memory_lint,
        "Check the memory store for the eight ways it drifts, heaviest first: a "
        "reference memory with no checkable source, a pointer to a session that no "
        "longer exists, a summary line too weak or too generic to retrieve on, a file "
        "missing from the index (which makes it permanently invisible), a link to a "
        "memory that does not exist, a file nothing links to, an index line pointing "
        "at nothing, and a link to something that does not exist yet. Also returns a "
        "read count per memory, which says more about what to keep than age does.",
        {"memory_dir": _DIR_PROP, "cwd": _CWD_PROP,
         "with_usage": {"type": "boolean",
                        "description": "include the per-memory read count; scans the "
                                       "transcripts, so it costs more. Default true."}},
        []),
    "memory_search": (
        memory_search,
        "Search the full text of every memory, not just the one-line summaries the "
        "recall step reads. Use it when you suspect something was written down but "
        "the index says nothing about it. Each hit comes back with the summary lines "
        "of the memories it links to, so one search surfaces a neighbourhood rather "
        "than a single file.",
        {"query": {"type": "string", "description": "text to look for; "
                                                    "case-insensitive substring"},
         "memory_dir": _DIR_PROP, "cwd": _CWD_PROP,
         "limit": {"type": "integer", "description": "default 10"},
         "neighbours": {"type": "boolean",
                        "description": "include linked memories' summaries. "
                                       "Default true."}},
        ["query"]),
    "memory_graph": (
        memory_graph,
        "How the memories link to each other: every memory with its link counts in "
        "and out, grouped by type, sorted fewest links first so the unconnected ones "
        "come out on top. Links pointing at names that do not exist are kept and "
        "marked, and their targets are listed separately.",
        {"memory_dir": _DIR_PROP, "cwd": _CWD_PROP},
        []),
    "memory_history": (
        memory_history,
        "How one memory changed over time, read from a git repository kept beside "
        "the store. Without a commit reference it lists the changes; with one it "
        "returns what the file said at that point. Pass init=true once to create the "
        "repository and take the first snapshot.",
        {"name": {"type": "string",
                  "description": "the memory's name, not its filename"},
         "memory_dir": _DIR_PROP, "cwd": _CWD_PROP,
         "limit": {"type": "integer", "description": "how many changes, default 20"},
         "at": {"type": "string",
                "description": "commit reference to read the file at"},
         "init": {"type": "boolean",
                  "description": "create the version history for this store"}},
        []),
}
