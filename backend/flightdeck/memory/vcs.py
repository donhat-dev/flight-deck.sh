"""A git repository for the memory store, kept OUTSIDE the directory it versions.

The harness scans the memory directory recursively and treats every .md it finds as a
memory, hidden directories included. So the repository lives at <project>/memory.git and
is reached with --git-dir/--work-tree, which leaves the scanned directory holding
nothing but the memories themselves.

Restores go through `show` rather than `git checkout --`: the local command guard
rejects checkout-discard, and reading a blob out is the safer operation anyway — it
cannot destroy an uncommitted edit.
"""
import subprocess
from pathlib import Path

_IDENTITY = ["-c", "user.email=flightdeck@localhost", "-c", "user.name=FlightDeck"]


def git_dir(memory_dir: Path) -> Path:
    return memory_dir.parent / "memory.git"


def is_initialised(memory_dir: Path) -> bool:
    return (git_dir(memory_dir) / "HEAD").is_file()


def _run(memory_dir: Path, args: list[str], check: bool = True):
    cmd = ["git", f"--git-dir={git_dir(memory_dir)}", f"--work-tree={memory_dir}",
           *_IDENTITY, *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=60)


def init(memory_dir: Path) -> dict:
    if is_initialised(memory_dir):
        return {"initialised": False, "git_dir": str(git_dir(memory_dir))}
    subprocess.run(["git", "init", "--quiet", "--bare", str(git_dir(memory_dir))],
                   capture_output=True, text=True, check=True, timeout=60)
    return {"initialised": True, "git_dir": str(git_dir(memory_dir))}


def commit(memory_dir: Path, message: str) -> dict:
    """Stage everything and commit. A no-change commit is reported, not attempted."""
    if not is_initialised(memory_dir):
        return {"error": "no memory repository yet; run memory_history --init first"}
    _run(memory_dir, ["add", "--all", "."])
    staged = _run(memory_dir, ["diff", "--cached", "--name-only"]).stdout.split()
    if not staged:
        return {"committed": False, "files": []}
    _run(memory_dir, ["commit", "--quiet", "-m", message])
    return {"committed": True, "files": staged}


def log(memory_dir: Path, filename: str, limit: int = 20) -> list[dict]:
    """Commits touching one file, newest first. --follow so a rename keeps its history."""
    proc = _run(memory_dir, ["log", "--follow", f"-{int(limit)}",
                             "--format=%H%x00%aI%x00%s", "--", filename], check=False)
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) == 3:
            out.append({"sha": parts[0], "date": parts[1], "message": parts[2]})
    return out


def show(memory_dir: Path, ref: str, filename: str) -> str:
    return _run(memory_dir, ["show", f"{ref}:{filename}"], check=False).stdout
