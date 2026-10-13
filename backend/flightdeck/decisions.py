"""Approval channel: pending-decision store + the escalation policy.

Pure SQLite, sqlite idiom (`?` placeholders), mirroring `flightdeck.missions.store`
(table creation, `db.open_write` / `db.open_read` usage, row->dict shape) so a
reader who already knows that module recognizes this one. The HTTP surface
lives in `routers/decisions.py`; this module owns the row shape and the
fail-open timeout rule.

**Schema is created lazily, per call, not at app startup.** Every other store
(`missions`, `treasures`, `radar`) gets its `init(write_conn)` called once from
`server.create_app()`. That file is off-limits to this track (three tracks
build on the event bus at once and `server.py` is shared wiring), so instead
every public function here calls `_ensure_schema(conn)` first. `CREATE TABLE IF
NOT EXISTS` is idempotent and cheap enough that paying it on every call is
simpler than inventing a second init path nothing else uses.

**Escalation is a hand-written allowlist, not a classifier.** `should_escalate`
matches the tool's command text against operator-authored regex lines in
`~/.flightdeck/approve.conf`. A missing or unreadable file means an empty
pattern list, which means nothing escalates — the dashboard being absent or
misconfigured must never turn into every tool call blocking on a human.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from flightdeck.events import BUS

# Fail-open timeout (see module docstring on `bin/fd-approve` for the full
# rationale): 90 seconds unattended and the call is ALLOWED. `dcg` and the
# other PreToolUse guards still fail-closed at their own layer beneath this
# one, so the strict guarantee for genuinely destructive commands is not lost
# by this channel choosing availability over blocking forever on an empty
# dashboard.
TIMEOUT_SECONDS = 90

STATES = ("pending", "approved", "rejected", "edited", "auto_released")

# Overridable so the test suite (and the live negative test) can point at a
# throwaway file instead of the operator's real `~/.flightdeck/approve.conf` —
# same reasoning as `conftest.py`'s hermetic-sqlite fixture: a test must not be
# able to read or depend on ambient machine state.
CONFIG_ENV_VAR = "FLIGHTDECK_APPROVE_CONF"
_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.flightdeck/approve.conf")


class AlreadyResolvedError(Exception):
    """Raised by `resolve()` when the decision is no longer pending.

    A second resolve on the same id is a double-submit (a slow UI, two tabs),
    not a new fact — the row must not silently flip states twice."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _age_seconds(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


# ---- schema ------------------------------------------------------------

def _ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          tool_name TEXT NOT NULL,
          command TEXT NOT NULL,
          pattern TEXT,
          cwd TEXT,
          created_at TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',
          resolved_at TEXT,
          edited_command TEXT,
          note TEXT
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_state ON decisions(state)")
    conn.commit()


def _row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "session_id": r["session_id"],
        "tool_name": r["tool_name"],
        "command": r["command"],
        "pattern": r["pattern"],
        "cwd": r["cwd"],
        "created_at": r["created_at"],
        "state": r["state"],
        "resolved_at": r["resolved_at"],
        "edited_command": r["edited_command"],
        "note": r["note"],
    }


# ---- escalation policy ---------------------------------------------------

def _config_path() -> str:
    return os.environ.get(CONFIG_ENV_VAR) or _DEFAULT_CONFIG_PATH

# path -> (mtime, [(raw_line, compiled_pattern), ...]). Module-level so a hot
# process (the FastAPI server) reloads on an mtime change without a restart,
# per the plan's "cache with an mtime check" requirement; process-lifetime
# scripts like `bin/fd-approve` just pay the read once per invocation.
_cache: dict = {"path": None, "mtime": None, "patterns": []}


def _load_patterns(path: str) -> list:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # Missing or unreadable file = empty pattern list = nothing escalates.
        _cache.update(path=path, mtime=None, patterns=[])
        return []
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["patterns"]
    patterns = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                try:
                    patterns.append((line, re.compile(line)))
                except re.error:
                    # A malformed operator-authored line must not crash every
                    # tool call; skip it rather than fail closed on all of them.
                    continue
    except OSError:
        patterns = []
    _cache.update(path=path, mtime=mtime, patterns=patterns)
    return patterns


def _command_text(tool_name: str, tool_input: dict) -> str:
    """Best-effort string to match escalation patterns against.

    Bash-shaped tools carry the real text in `tool_input["command"]`; anything
    else (an MCP tool, a file-edit tool) falls back to a compact JSON dump so a
    pattern can still match on the tool_input shape (e.g. a file path)."""
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        return tool_input["command"]
    try:
        return json.dumps(tool_input or {}, sort_keys=True)
    except TypeError:
        return str(tool_input)


def should_escalate(tool_name: str, tool_input: dict) -> Optional[str]:
    """Return the matched pattern (for the audit trail), or None.

    None is the default for every tool call that does not match a line in
    `approve.conf` — that is the "everything else passes through with zero
    latency" half of the plan, and it is also what a missing config file
    produces, since `_load_patterns` returns `[]` for it."""
    text = _command_text(tool_name, tool_input)
    for raw, compiled in _load_patterns(_config_path()):
        if compiled.search(text):
            return raw
    return None


# ---- auto-release (fail-open timeout) ------------------------------------

def _maybe_auto_release(conn, row: dict) -> dict:
    """Flip a stale `pending` row to `auto_released` on read.

    The transition happens lazily, on whichever read notices the deadline has
    passed, rather than on a background sweep: the hook's own poll loop reads
    this same row every ~1s while waiting, so in practice the row flips at (or
    just before) the moment the hook's local 90s timer fires and allows the
    call — the row and the hook's own fail-open decision agree without the two
    having to coordinate directly.
    """
    if row["state"] != "pending":
        return row
    age = _age_seconds(row["created_at"])
    if age is None or age < TIMEOUT_SECONDS:
        return row
    now = _now()
    # Guarded by `state='pending'` so two concurrent readers racing this check
    # do not both emit `decision.resolved` for the same row.
    cur = conn.execute(
        "UPDATE decisions SET state='auto_released', resolved_at=? "
        "WHERE id=? AND state='pending'",
        (now, row["id"]),
    )
    conn.commit()
    if not cur.rowcount:
        return get(conn, row["id"]) or row
    row = dict(row, state="auto_released", resolved_at=now)
    BUS.emit("decision.resolved", source="hook-timeout", **row)
    return row


# ---- store ---------------------------------------------------------------

def create(conn, *, tool_name: str, command: str, session_id: Optional[str] = None,
           pattern: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Record a newly escalated tool call and publish it to the dock."""
    _ensure_schema(conn)
    did = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO decisions (id, session_id, tool_name, command, pattern, cwd, "
        "created_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
        (did, session_id, tool_name, command, pattern, cwd, now),
    )
    conn.commit()
    row = get(conn, did)
    BUS.emit("decision.pending", source="hook", **row)
    return row


def get(conn, did: str) -> Optional[dict]:
    _ensure_schema(conn)
    r = conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
    if not r:
        return None
    return _maybe_auto_release(conn, _row_to_dict(r))


def list_pending(conn) -> list:
    """Open decisions, for the dock's initial render (live updates ride the bus).

    Each row is passed through `_maybe_auto_release` before being returned, and
    one that just flipped to `auto_released` is dropped from the result rather
    than shown with its new state — the dock's job is to list what still needs a
    human, and the SSE `decision.resolved` event (already emitted by the flip)
    is what tells an open dock to stop rendering it.
    """
    _ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM decisions WHERE state='pending' ORDER BY created_at ASC"
    ).fetchall()
    out = []
    for r in rows:
        d = _maybe_auto_release(conn, _row_to_dict(r))
        if d["state"] == "pending":
            out.append(d)
    return out


def resolve(conn, did: str, *, action: str, edited_command: Optional[str] = None,
            note: Optional[str] = None) -> Optional[dict]:
    """A human answers approve/reject/edit. Returns None for an unknown id,
    raises `AlreadyResolvedError` for a decision that is no longer pending."""
    _ensure_schema(conn)
    row = get(conn, did)
    if row is None:
        return None
    if row["state"] != "pending":
        raise AlreadyResolvedError(did)
    state = {"approve": "approved", "reject": "rejected", "edit": "edited"}[action]
    now = _now()
    conn.execute(
        "UPDATE decisions SET state=?, resolved_at=?, edited_command=?, note=? "
        "WHERE id=?",
        (state, now, edited_command, note, did),
    )
    conn.commit()
    row = get(conn, did)
    BUS.emit("decision.resolved", source="api", **row)
    return row
