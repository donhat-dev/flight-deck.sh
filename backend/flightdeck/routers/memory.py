"""Memory API: the four read-only views over the auto-memory store.

Thin on purpose. `flightdeck.memory.mcp_server` already holds the whole domain and
its tests; this router only turns a query string into those four calls and an
`{"error": ...}` dict into a real status code. Nothing here reads the ledger, takes
the write lock, or opens a database connection — the store is files on disk.

**Read-only, and that is a boundary, not an accident.** `memory_history` also has an
`init=true` mode that creates the sidecar git repository. It is deliberately not
reachable from HTTP: it writes, and every endpoint in this module reads.

**Which store a request means.** `cwd` picks the project whose memories to read, the
same way the agent surface does. Omitted, it falls back to `FLIGHTDECK_WORKSPACE`
before letting the domain resolve it from the process directory — every run mode
starts uvicorn with the working directory at `backend/`, which is not a project with
memories, so without this fallback the default request would always report "no memory
store". The same variable already pins the workspace for Diff, Comms and Manuals.
"""
import os

from fastapi import APIRouter, HTTPException, Query

from flightdeck.memory import mcp_server

router = APIRouter(prefix="/api/memory", tags=["memory"])

#: The domain's own words for a failure, mapped to the status a client can act on.
#: Matched on a phrase inside the message rather than on an exception type, because
#: these functions return a dict; the text is theirs and is passed through untouched.
#: Anything unrecognised falls to 400, which is the safe read of "the request asked
#: for something the store cannot give".
_STATUS = (
    ("no memory store at", 404),
    ("no memory named", 404),
    ("empty query", 400),
    ("not under version control", 409),
)


def _project(cwd: str | None) -> str | None:
    return cwd or os.environ.get("FLIGHTDECK_WORKSPACE") or None


def _checked(result: dict) -> dict:
    """Pass a result through, or raise the error it is carrying."""
    error = result.get("error") if isinstance(result, dict) else None
    if not error:
        return result
    status = next((code for phrase, code in _STATUS if phrase in error), 400)
    raise HTTPException(status_code=status, detail=error)


@router.get("/lint")
def lint(cwd: str | None = None, with_usage: bool = True):
    """Everything that drifted in the store, heaviest problem first.

    `with_usage` scans the transcripts to count how often each memory was read, so it
    is the slow half; the tab asks for it because "never read" is the retention signal
    the list is sorted on.
    """
    return _checked(mcp_server.memory_lint(cwd=_project(cwd), with_usage=with_usage))


@router.get("/search")
def search(query: str = Query(..., description="Text to look for. Case-insensitive."),
           cwd: str | None = None, limit: int = 10, neighbours: bool = True):
    """Full text of every memory, not just the summary lines recall reads."""
    return _checked(mcp_server.memory_search(
        query, cwd=_project(cwd), limit=limit, neighbours=neighbours))


@router.get("/graph")
def graph(cwd: str | None = None):
    """Every memory with its link counts, plus the links that point at nothing."""
    return _checked(mcp_server.memory_graph(cwd=_project(cwd)))


@router.get("/history")
def history(name: str = Query(..., description="The memory's name, not its filename."),
            cwd: str | None = None, limit: int = 20, at: str | None = None):
    """How one memory changed. With `at`, what it said at that commit.

    `init` is not exposed: it creates the sidecar repository, and this router reads.
    """
    return _checked(mcp_server.memory_history(
        name=name, cwd=_project(cwd), limit=limit, at=at))
