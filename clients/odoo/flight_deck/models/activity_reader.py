"""Agent busy time, read from a transcript file.

The same rules as `python/flightdeck/activity.py`, so the two sides report
the same hours for the same files:

- A turn starts at a prompt a person wrote and ends at the agent's final
  `end_turn`. A system-origin turn (a task notification, a hook) continues
  the task and makes the agent busy again.
- Busy time is the sum of gaps between consecutive records inside an open
  turn, each gap capped: a long gap is somebody away, not the agent working.
  The wait for a person to answer `AskUserQuestion` is not counted.
- A subagent file counts on its own, so four agents busy for an hour are four
  hours. The main agent keeps counting while it waits on them.
- Resumed and forked sessions start with a copy of the history they came
  from. That work was done, and counted, in the session it came from.

Kept free of Odoo so the rules can be read, and tested, on their own.
"""
import json
import re
from datetime import datetime, timedelta, timezone

IDLE_CAP_SECONDS = 15 * 60
ASK_TOOL = "AskUserQuestion"
# Enough of a prompt, and of the agent's last message, to tell what a turn was
# about; the classifier reads these, not the whole transcript.
PROMPT_TEXT_CHARS = 2000
FINAL_HEAD_CHARS = 400
STEER_TEXT_CHARS = 2000

# A user line that is machinery, not a person. Same list as the backend and as
# `session_turn.PROMPT_WHERE`.
_MACHINE_PREFIXES = ("<", "[Request interrupted", "[Image:", "Caveat:",
                     "This session is being continued from a previous conversation")
# Editor state the IDE extension puts in front of a prompt.
_IDE_BLOCK = re.compile(r"\s*<(ide_[a-z_]+)>.*?</\1>", re.S)
AGENT_FILE = re.compile(r"agent-([^/]+)\.jsonl$")


def parse_ts(value):
    """A transcript timestamp as an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _user_text(obj):
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


def _prompt_body(text):
    while True:
        found = _IDE_BLOCK.match(text)
        if not found:
            return text
        text = text[found.end():]


def is_human_prompt(obj):
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return False
    # A compaction summary carries `turnOrigin: human` and the promptId of the
    # prompt it continues; taking it as a prompt restarts that turn.
    if obj.get("isCompactSummary"):
        return False
    origin = obj.get("turnOrigin")
    if origin:
        # `sdk` is a prompt handed to `claude -p` or to a runner session: the
        # turn's own prompt, typed elsewhere. Peers, task notifications and
        # scheduled wake-ups land inside a turn and do not start one.
        return origin in ("human", "sdk")
    text = _prompt_body(_user_text(obj) or "").strip()
    return bool(text) and not text.startswith(_MACHINE_PREFIXES)


def _steer_text(obj):
    """The text of a message a person typed while the agent worked, or None.

    Claude Code does not write it as a user line: it is an `attachment` of
    type `queued_command`, with no prompt id. Task notifications and messages
    from other sessions arrive the same way and are left out, and so is a
    slash command, which asks for no work.
    """
    attachment = obj.get("attachment") or {}
    if (obj.get("type") != "attachment" or attachment.get("type") != "queued_command"
            or attachment.get("commandMode") != "prompt"):
        return None
    prompt = attachment.get("prompt")
    if isinstance(prompt, list):
        prompt = "\n".join(b.get("text") or "" for b in prompt
                           if isinstance(b, dict) and b.get("type") == "text")
    text = _prompt_body(prompt or "").strip()
    if not text or text.startswith(_MACHINE_PREFIXES) or text.startswith("/"):
        return None
    return text[:STEER_TEXT_CHARS]


def slim_records(path, offset, limit=None):
    """The fields the tracker reads, one dict per line after `offset`.

    Returns `(records, bytes_read)`; only whole lines are consumed. `limit`
    caps the bytes read in one pass, as `transcript_reader.read_usage` does.
    """
    records, consumed = [], 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            at = offset + consumed
            consumed += len(raw)
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                obj = None
            if isinstance(obj, dict) and (obj.get("type") in ("user", "assistant", "system")
                                          or _steer_text(obj)):
                rec = _slim(obj)
                rec["offset"] = at
                records.append(rec)
            if limit is not None and consumed >= limit:
                break
    return records, consumed


def _slim(obj):
    message = obj.get("message") or {}
    content = message.get("content")
    human = is_human_prompt(obj)
    return {
        "type": obj.get("type"),
        "uuid": obj.get("uuid"),
        "ts": obj.get("timestamp"),
        "session_id": obj.get("sessionId"),
        "prompt_id": obj.get("promptId") or (obj.get("uuid") if human else None),
        "human": human,
        "head": _prompt_body(_user_text(obj) or "").strip()[:PROMPT_TEXT_CHARS] if human else None,
        "cwd": obj.get("cwd"),
        "branch": obj.get("gitBranch"),
        # Each text block is a line of its own, and the one holding the answer
        # is not always the line that carries `end_turn`.
        "text": _assistant_text(content)[:FINAL_HEAD_CHARS] if obj.get("type") == "assistant" else None,
        "msg_id": message.get("id") if obj.get("type") == "assistant" else None,
        "billed": bool(message.get("usage")),
        "stop": message.get("stop_reason"),
        "steer": _steer_text(obj),
        "asks": isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == ASK_TOOL
            for b in content),
        "question": _ask_question(content),
    }


def _ask_question(content):
    """The first question an `AskUserQuestion` call in `content` puts to the person."""
    for b in content if isinstance(content, list) else ():
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == ASK_TOOL:
            questions = (b.get("input") or {}).get("questions") or [{}]
            return (questions[0].get("question") or "")[:FINAL_HEAD_CHARS] or None
    return None


def _assistant_text(content):
    if isinstance(content, str):
        return content.strip()
    return "\n".join(
        (b.get("text") or "").strip() for b in content or []
        if isinstance(b, dict) and b.get("type") == "text").strip()


class Tracker:
    """Busy-time state for one file, carried from one read to the next.

    `state` is what `to_state` returned last time, or None for a file read
    from its start. Busy seconds collect in `busy`, keyed on
    `(prompt_id, UTC hour start)`; an hour is the grain because every local
    day boundary a person reads falls on one.
    """

    def __init__(self, sub, state=None, cap=IDLE_CAP_SECONDS):
        state = state or {}
        self.sub = sub
        self.cap = cap
        self.last_ts = parse_ts(state.get("last_ts"))
        self.in_turn = bool(state.get("in_turn"))
        self.wait_human = bool(state.get("wait_human"))
        self.copy_mode = bool(state.get("copy_mode"))
        self.prompt_id = state.get("prompt_id") or ""
        self.session_id = state.get("session_id")
        self.busy = {}
        # Prompts this read opened, with when, what and where, for the turn
        # rows; and what this read learnt about how a turn ended.
        self.opened = {}
        self.closed = {}
        # Messages a person typed while a turn ran, with the turn's prompt id.
        self.steers = []

    def to_state(self):
        return {
            "last_ts": self.last_ts.isoformat() if self.last_ts else None,
            "in_turn": self.in_turn,
            "wait_human": self.wait_human,
            "copy_mode": self.copy_mode,
            "prompt_id": self.prompt_id,
            "session_id": self.session_id,
        }

    def feed(self, rec, copied=False, foreign_prompt=False):
        """Advance on one slim record.

        `copied` is True when the record's API message already belongs to
        another session; `foreign_prompt` when its prompt does.
        """
        t = parse_ts(rec["ts"])
        if t is None:
            return
        if self.session_id is None and rec["session_id"]:
            self.session_id = rec["session_id"]
        kind = rec["type"]
        if kind == "attachment":
            # A steer belongs to the running turn and leaves busy time as it is.
            if rec.get("steer") and not self.sub and self.prompt_id and not self.copy_mode:
                self.steers.append({"uuid": rec.get("uuid"), "at": t, "text": rec["steer"],
                                    "prompt_id": self.prompt_id})
            return

        if self.sub:
            if not self.prompt_id and rec["prompt_id"]:
                self.prompt_id = rec["prompt_id"]
            self.in_turn = True
        elif rec["human"]:
            if self.prompt_id and not self.copy_mode:
                # The next prompt is where the previous turn's lines stop.
                end = self.closed.setdefault(self.prompt_id, {})
                end["byte_end"] = rec.get("offset")
                # An interrupted turn has no `end_turn`; its last line is its end.
                end["last"] = self.last_ts
            self.prompt_id = rec["prompt_id"] or ""
            self.copy_mode = foreign_prompt
            if not foreign_prompt and self.prompt_id:
                self.opened.setdefault(self.prompt_id, {
                    "started": t, "text": rec["head"], "cwd": rec.get("cwd"),
                    "branch": rec.get("branch"), "byte_start": rec.get("offset"),
                })
            self.in_turn, self.wait_human, self.last_ts = True, False, t
            return
        elif kind == "user" and not self.in_turn and rec["prompt_id"]:
            self.in_turn, self.wait_human, self.last_ts = True, False, t
            return

        # The gap that ends a copied block spans the pause before the resume,
        # so it is skipped too.
        was_copy = self.copy_mode
        if copied:
            self.copy_mode = True
        elif kind == "assistant" and rec["billed"]:
            self.copy_mode = False
        if (self.in_turn and self.last_ts is not None and t > self.last_ts
                and not self.wait_human and not was_copy and not self.copy_mode):
            self._add(self.last_ts, min(t, self.last_ts + timedelta(seconds=self.cap)))
        if self.last_ts is None or t > self.last_ts:
            self.last_ts = t

        if kind == "assistant":
            self.wait_human = rec["asks"]
            if rec["asks"] and not self.sub and not self.copy_mode and self.prompt_id:
                # The agent now waits for the person's answer (turn_notify tells them).
                self.closed.setdefault(self.prompt_id, {})["asked"] = {
                    "at": t, "question": rec.get("question")}
            if rec.get("text") and not self.copy_mode and self.prompt_id:
                self.closed.setdefault(self.prompt_id, {})["final"] = rec["text"]
            if (not self.sub and rec["stop"] == "end_turn" and self.in_turn
                    and not self.copy_mode):
                self.in_turn = False
                if self.prompt_id:
                    self.closed.setdefault(self.prompt_id, {})["ended"] = t
        elif kind == "user":
            self.wait_human = False

    def _add(self, start, end):
        while start < end:
            hour = start.replace(minute=0, second=0, microsecond=0)
            stop = min(end, hour + timedelta(hours=1))
            key = (self.prompt_id or "", hour)
            self.busy[key] = self.busy.get(key, 0.0) + (stop - start).total_seconds()
            start = stop
