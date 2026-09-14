"""Send one message into an existing session by running the Claude Code CLI.

`claude --resume <id> -p -- <message>` appends the exchange to that session's own
JSONL under the SAME session id, so the transcript endpoints and the session
viewer pick the new turns up with no extra plumbing.

Test surface, kept apart from `routers/sessions.py` because everything there is
read-only and this spawns a process and writes.

`session_id` is parsed as a UUID and the message is passed after `--` for the
same reason: both land in argv where the CLI parses a leading `-` as a flag.

Two writers on one session lose turns, and there are two ways to get them. A
live interactive session holds its conversation in memory and rewrites the same
file. And two sends at once each resume the same last turn, so both write a
child of the same parent: the records all survive, but the transcript is a
parent chain, so a reader walks one branch and the other turns stop being
visible while still sitting in the file. Measured here — two sends ~0.7s apart
produced `parent b329f152 -> user:'ping ping' | user:'ping'`.

Both checks fail closed: a liveness check that cannot run refuses the send
rather than guessing, and `allow_live` is the single explicit override.
"""
import json
import os
import subprocess
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from flightdeck import transcript

router = APIRouter(tags=["send"])

_DEFAULT_TIMEOUT = 300

# Single-process, 1-worker by design (see runtime.py), so an in-process lock
# per session is the whole guard. It serializes THIS endpoint only — a
# `claude --resume -p` run started from a terminal still races, and
# `claude agents --json` does not list -p runs, so nothing here can see it.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class LivenessUnknown(Exception):
    """The interactive-session check could not run."""


def _session_lock(session_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(session_id, threading.Lock())


def _claude_bin() -> str:
    return os.environ.get("TOKEN_AUDIT_CLAUDE_BIN", "claude")


def _session_cwd(path) -> str | None:
    """The directory the session ran in, read from the transcript itself.

    Taken from a record rather than decoded from the project directory name:
    that encoding replaces every separator with a dash, so it cannot be
    reversed for a path that contains one.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _live_pid(session_id: str) -> int | None:
    """The pid of an interactive session holding this conversation, else None.

    Raises LivenessUnknown when the check itself failed, so "no live session"
    is never inferred from a failure to look.
    """
    try:
        proc = subprocess.run([_claude_bin(), "agents", "--json"],
                              capture_output=True, text=True, timeout=30)
    except Exception as e:
        raise LivenessUnknown(str(e)) from e
    if proc.returncode != 0:
        raise LivenessUnknown((proc.stderr or "").strip()[:200] or
                              f"exit {proc.returncode}")
    try:
        agents = json.loads(proc.stdout)
    except ValueError as e:
        raise LivenessUnknown(f"unparsable agents listing: {e}") from e
    for a in agents:
        if a.get("sessionId") == session_id and a.get("kind") == "interactive":
            return a.get("pid") or -1
    return None


@router.post("/api/session/{session_id}/send")
def session_send(request: Request, session_id: str, message: str,
                 timeout: int = _DEFAULT_TIMEOUT, allow_live: bool = False):
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id must be a UUID")
    if not message.strip():
        raise HTTPException(status_code=400, detail="message is empty")

    path = transcript.find_session_file(
        request.app.state.cfg["projects_dir"], session_id)
    if not path:
        raise HTTPException(status_code=404, detail="session not found")

    if not allow_live:
        try:
            pid = _live_pid(session_id)
        except LivenessUnknown as e:
            raise HTTPException(
                status_code=503,
                detail=f"cannot verify session liveness ({e}); pass "
                       "allow_live=true to send anyway")
        if pid is not None:
            raise HTTPException(
                status_code=409,
                detail=f"session is open in an interactive process (pid {pid}); "
                       "writing to it can lose turns. Pass allow_live=true to "
                       "override.")

    lock = _session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409,
                            detail="another send is in flight for this session; "
                                   "retry when it finishes")

    argv = [_claude_bin(), "--resume", session_id, "-p", "--", message]
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=_session_cwd(path),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504,
                            detail=f"claude did not finish within {timeout}s")
    except FileNotFoundError:
        raise HTTPException(status_code=500,
                            detail=f"claude binary not found: {_claude_bin()}")
    finally:
        lock.release()

    elapsed = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail={"exit_code": proc.returncode,
                    "stderr": (proc.stderr or "")[-2000:],
                    "duration_ms": elapsed})
    return {
        "session_id": session_id,
        "reply": proc.stdout.strip(),
        "duration_ms": elapsed,
    }
