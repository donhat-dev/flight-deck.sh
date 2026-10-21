"""How one memory changed over time, and what it said before.

A memory file only states what is believed now. The sidecar repository is what answers
"what did this say three weeks ago, and why did it change" — the temporal dimension a
plain file store cannot give, obtained from git rather than from a separate temporal
index.

Reading an old version goes through `git show`, never `git checkout --`: showing a blob
cannot destroy an uncommitted edit, and the local command guard rejects checkout-discard
anyway.
"""
from pathlib import Path

from flightdeck.memory import store, vcs


def run(memory_dir, name: str, limit: int = 20, at: str | None = None) -> dict:
    memory_dir = Path(memory_dir)
    match = next((m for m in store.load_all(memory_dir) if m.name == name), None)
    if match is None:
        return {"error": f"no memory named {name!r} in {memory_dir}"}
    if not vcs.is_initialised(memory_dir):
        return {"error": "this store is not under version control yet; "
                         "run memory_history --init to create the repository"}

    out = {"memory": name, "filename": match.filename,
           "commits": vcs.log(memory_dir, match.filename, limit=limit)}
    if at:
        out["at"] = at
        out["content"] = vcs.show(memory_dir, at, match.filename)
    return out


def init(memory_dir) -> dict:
    """Create the sidecar repository and take the first snapshot in one act."""
    memory_dir = Path(memory_dir)
    created = vcs.init(memory_dir)
    committed = vcs.commit(memory_dir, "snapshot: memory store before tooling")
    return {**created, **committed}
