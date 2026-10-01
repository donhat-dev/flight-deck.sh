"""Turns and agent busy time, read from the same transcript pass as usage.

Two things are derived here while `ingest` walks a jsonl file line by line:

- **Turns.** A turn starts at a prompt a person wrote and runs until the agent's
  final `end_turn`. A prompt sent before the agent has replied joins the open turn
  (the agent answers both at once). Every billed row read in between is stamped with the turn's
  prompt id, which is what cost per task is summed over. Subagent files carry the
  parent turn's `promptId` on their own records, so their rows join the same turn.
- **Busy time.** An agent is busy from the record that opens a turn to the one
  that ends it. Busy time is the sum of gaps between consecutive records inside an
  open turn, each gap capped (a long gap is someone away, not the agent working),
  minus the wait for a person to answer `AskUserQuestion`. A subagent file counts
  on its own, so four agents busy for an hour are four hours; the main agent keeps
  counting while it waits on them. Every model counts 1:1.

Incremental reads resume mid-file, so the tracker's state is persisted in the
`files` row (`act_*` columns) and restored on the next read.
"""
import os
import re
from datetime import datetime, timedelta

DEFAULT_IDLE_CAP_MINUTES = 15.0
ASK_TOOL = "AskUserQuestion"

# A user line that is machinery, not a person: tags the CLI injects (commands,
# reminders, task notifications, editor state), the interrupt marker, the image
# trailer and the caveat block. Same rule as the Odoo module's PROMPT_WHERE.
_MACHINE_PREFIXES = ("<", "[Request interrupted", "[Image:", "Caveat:",
                     "This session is being continued from a previous conversation")
# Some resumes re-append an earlier stretch of the session to the same file, with
# the original uuids and timestamps. A line this much older than the newest one
# read is such a replay.
REPLAY_SLACK = timedelta(seconds=120)
PROMPT_HEAD_CHARS = 2000
FINAL_HEAD_CHARS = 1000

_AGENT_FILE = re.compile(r"agent-([^/]+)\.jsonl$")
# The VS Code extension puts the open file / selection in front of the prompt.
_IDE_BLOCK = re.compile(r"\s*<(ide_[a-z_]+)>.*?</\1>", re.S)


def idle_cap_seconds(cfg: dict | None = None) -> float:
    """Idle-gap cap in seconds: env TOKEN_AUDIT_AGENT_IDLE_CAP_MIN, then
    config `agent_idle_cap_minutes`, then 15 minutes."""
    raw = os.environ.get("TOKEN_AUDIT_AGENT_IDLE_CAP_MIN")
    if raw is None and cfg:
        raw = cfg.get("agent_idle_cap_minutes")
    try:
        minutes = float(raw) if raw is not None else DEFAULT_IDLE_CAP_MINUTES
    except (TypeError, ValueError):
        minutes = DEFAULT_IDLE_CAP_MINUTES
    return max(minutes, 0.0) * 60.0


def _parse_ts(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def user_text(obj: dict) -> str | None:
    """The text a user line carries, or None when it is a tool result."""
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            return None
        if block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(parts)


def prompt_body(text: str) -> str:
    """The prompt without the IDE context blocks in front of it."""
    while True:
        m = _IDE_BLOCK.match(text)
        if not m:
            return text
        text = text[m.end():]


def is_human_prompt(obj: dict) -> bool:
    """True for a line a person typed (or picked) as a new prompt.

    Newer CLI versions say so in `turnOrigin`; older lines are judged by text.
    """
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return False
    origin = obj.get("turnOrigin")
    if origin:
        return origin == "human"
    text = prompt_body(user_text(obj) or "").strip()
    return bool(text) and not text.startswith(_MACHINE_PREFIXES)


def _assistant_text(obj: dict) -> str:
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join((b.get("text") or "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")


def _asks_a_person(obj: dict) -> bool:
    content = (obj.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == ASK_TOOL
        for b in content)


class FileTracker:
    """Turn and busy-time state for one jsonl file during one ingest pass."""

    def __init__(self, conn, path: str, rec, start: int, cap_seconds: float):
        self.conn = conn
        self.cap = cap_seconds
        m = _AGENT_FILE.search(path)
        self.sub = "/subagents/" in path and m is not None
        self.agent_id = m.group(1) if self.sub else ""
        resumed = rec is not None and start > 0
        self.last_ts = _parse_ts(rec["act_last_ts"]) if resumed and rec.get("act_last_ts") else None
        self.in_turn = bool(rec["act_in_turn"]) if resumed and rec.get("act_in_turn") else False
        self.wait_human = bool(rec["act_wait_human"]) if resumed and rec.get("act_wait_human") else False
        self.prompt_id = (rec.get("act_prompt_id") if resumed else None) or ""
        # Whether the agent has replied in the open turn; until it has, a further
        # prompt joins this turn instead of opening one.
        self.answered = rec.get("act_answered") != 0 if resumed else True
        # A read from offset 0 recounts the whole file, so its earlier
        # contribution is dropped once the session id is known.
        self._reset_pending = not resumed
        self.session_id = None
        # Resumed and forked sessions start with a copy of the history they came
        # from. That work was done (and counted) in the original session.
        self.copy_mode = False
        self.busy: dict = {}          # (prompt_id, day) -> seconds
        self._final = None            # (turn_id, ts, text) of the last closed turn

    # -- attribution for billed rows --------------------------------------
    def stamp(self) -> dict:
        return {"prompt_id": self.prompt_id or None, "agent_id": self.agent_id or None}

    def is_replay(self, obj: dict) -> bool:
        """True for a line re-appended from earlier in the same file."""
        t = _parse_ts(obj.get("timestamp"))
        return t is not None and self.last_ts is not None and t < self.last_ts - REPLAY_SLACK

    # -- the state machine -------------------------------------------------
    def feed(self, obj: dict, copied: bool = False) -> None:
        """Advance on one line. `copied` is True when the line's billed row
        already belongs to another session (a resume/fork copy)."""
        kind = obj.get("type")
        if kind not in ("user", "assistant", "system"):
            return
        t = _parse_ts(obj.get("timestamp"))
        if t is None or self.is_replay(obj):
            # A replay was counted, and its turn recorded, when first read.
            return
        if self.session_id is None and obj.get("sessionId"):
            self.session_id = obj["sessionId"]
            if self._reset_pending:
                self.conn.execute(
                    "DELETE FROM agent_activity WHERE session_id=? AND agent_id=? "
                    "AND method='transcript'", (self.session_id, self.agent_id))
                self._reset_pending = False

        if self.sub:
            if not self.prompt_id and obj.get("promptId"):
                self.prompt_id = obj["promptId"]
            self.in_turn = True
        elif is_human_prompt(obj):
            pid = obj.get("promptId") or obj.get("uuid") or ""
            if self.in_turn and self.prompt_id and (pid == self.prompt_id or not self.answered):
                self._fold(obj, pid)
            else:
                self.prompt_id = pid
                self.copy_mode = self._open_turn(obj)
                self.in_turn, self.wait_human, self.last_ts = True, False, t
                self.answered = False
                return
        elif kind == "user" and not self.in_turn and obj.get("promptId"):
            # A system-origin turn (task notification, hook) continues the
            # current task; the agent starts working again from here.
            self.in_turn, self.wait_human, self.last_ts = True, False, t
            self.answered = True
            return

        # The gap that ends a copied block spans the pause before the resume,
        # so it is skipped too.
        was_copy = self.copy_mode
        if copied:
            self.copy_mode = True
        elif kind == "assistant" and (obj.get("message") or {}).get("usage"):
            self.copy_mode = False
        if self.in_turn and self.last_ts is not None and t > self.last_ts \
                and not self.wait_human and not was_copy and not self.copy_mode:
            self._add(self.last_ts, min(t, self.last_ts + timedelta(seconds=self.cap)))
        if self.last_ts is None or t > self.last_ts:
            self.last_ts = t

        if kind == "assistant":
            self.answered = True
            self.wait_human = _asks_a_person(obj)
            stop = (obj.get("message") or {}).get("stop_reason")
            if not self.sub and stop == "end_turn" and self.in_turn and not self.copy_mode:
                self.in_turn = False
                self._final = (self.prompt_id, obj.get("timestamp"), _assistant_text(obj))
                self._close_turn()
        elif kind == "user":
            self.wait_human = False

    def _add(self, start, end) -> None:
        # Split at UTC midnight: the dashboard's days are UTC dates (ts[:10]).
        while start < end:
            midnight = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                           microsecond=0)
            stop = min(end, midnight)
            key = (self.prompt_id or "", start.date().isoformat())
            self.busy[key] = self.busy.get(key, 0.0) + (stop - start).total_seconds()
            start = stop

    def _open_turn(self, obj: dict) -> bool:
        """Record the turn; return True when it already belongs to another
        session, i.e. this prompt is part of a copied history."""
        known = self.conn.execute(
            "SELECT session_id FROM turns WHERE turn_id=?", (self.prompt_id,)).fetchone()
        text = prompt_body(user_text(obj) or "").strip()
        if known is not None:
            if known["session_id"] != obj.get("sessionId"):
                return True
            # A re-read of the owning session refreshes what the prompt rule reads.
            self.conn.execute(
                "UPDATE turns SET prompt_head=?, cwd=?, git_branch=? WHERE turn_id=?",
                (text[:PROMPT_HEAD_CHARS], obj.get("cwd"), obj.get("gitBranch"), self.prompt_id))
            return False
        self.conn.execute(
            "INSERT INTO turns (turn_id, session_id, started_at, prompt_head, cwd, git_branch) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(turn_id) DO NOTHING",
            (self.prompt_id, obj.get("sessionId"), obj.get("timestamp"),
             text[:PROMPT_HEAD_CHARS], obj.get("cwd"), obj.get("gitBranch")))
        return False

    def _fold(self, obj: dict, pid: str) -> None:
        """Add a prompt to the open turn."""
        if self.copy_mode:
            return
        if pid != self.prompt_id:
            # An older ingest may have stored the joined prompt as its own turn.
            self.conn.execute("DELETE FROM turns WHERE turn_id=? AND session_id=?",
                              (pid, obj.get("sessionId")))
        text = prompt_body(user_text(obj) or "").strip()
        if text:
            self.conn.execute(
                "UPDATE turns SET prompt_head=substr(COALESCE(prompt_head, '') || ?, 1, ?) "
                "WHERE turn_id=?", ("\n\n" + text, PROMPT_HEAD_CHARS, self.prompt_id))

    def _close_turn(self) -> None:
        turn_id, ts, text = self._final
        if not turn_id:
            return
        self.conn.execute(
            "UPDATE turns SET ended_at=?, final_head=? WHERE turn_id=? "
            "AND (ended_at IS NULL OR ended_at < ?)",
            (ts, (text or "").strip()[:FINAL_HEAD_CHARS] or None, turn_id, ts))

    # -- persistence -------------------------------------------------------
    def flush(self) -> dict:
        """Write accumulated busy time; return the `act_*` state to persist."""
        if self.session_id:
            for (prompt_id, day), sec in self.busy.items():
                if sec <= 0:
                    continue
                self.conn.execute(
                    "INSERT INTO agent_activity (session_id, agent_id, prompt_id, day, "
                    "method, busy_seconds) VALUES (?,?,?,?,'transcript',?) "
                    "ON CONFLICT (session_id, agent_id, prompt_id, day, method) DO UPDATE "
                    "SET busy_seconds = agent_activity.busy_seconds + excluded.busy_seconds",
                    (self.session_id, self.agent_id, prompt_id, day, sec))
        self.busy = {}
        return {
            "act_last_ts": self.last_ts.isoformat() if self.last_ts else None,
            "act_in_turn": int(self.in_turn),
            "act_wait_human": int(self.wait_human),
            "act_prompt_id": self.prompt_id or None,
            "act_answered": int(self.answered),
        }
