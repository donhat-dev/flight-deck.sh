"""Turn Claude Code transcript lines into transcript rows.

No ORM here on purpose: the file rules are the fiddly part and they are easier
to reason about, and to test, on their own.

Three rules carried over from the reader that already runs against these files:
a line without a trailing newline is still being written and its bytes are left
for the next pass; `seq` is the absolute line number in the file, so resuming
mid-file continues the count; and a torn write can leave a run of NUL bytes that
`json.loads` reads as UTF-32, which raises UnicodeDecodeError rather than a JSON
error and used to take the whole read down.
"""
import json
import os
import re

CHAT_TYPES = ("user", "assistant")
MAX_TEXT_CHARS = 24000
MAX_TOOL_INPUT_CHARS = 4000
MAX_TOOL_OUTPUT_CHARS = 8000
# What a browser will render as an image without being able to run anything.
# SVG is deliberately absent: served same-origin it is a script.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")

# The tool that spawns an agent, and the two shapes its answer comes back in.
AGENT_TOOL = "Agent"
# A background spawn answers at once with the id of the agent it started; the
# real outcome arrives later as a task notification.
LAUNCH_RE = re.compile(
    r"^Async agent launched successfully\.[^\n]*\nagentId: ([A-Za-z0-9_-]+)")
# The tool that runs a whole script of agents. It writes them into a directory
# of its own and names that directory in its answer.
WORKFLOW_TOOL = "Workflow"
AGENT_TOOLS = (AGENT_TOOL, WORKFLOW_TOOL)
WORKFLOW_RE = re.compile(r"Transcript dir: \S*/workflows/(wf_[A-Za-z0-9_-]+)")
# `export const meta = { name: '...' }` opens every workflow script.
WORKFLOW_NAME_RE = re.compile(r"""\bname:\s*['"]([^'"\n]{1,200})['"]""")
# The tool one session messages another with. Its answer carries the id the
# receiving session sees the message under.
PEER_TOOL = "SendMessage"
NOTIFY_FIELDS = ("task-id", "tool-use-id", "status", "output-file", "summary")
# What the notification reports about the run itself.
USAGE_FIELDS = ("subagent_tokens", "tool_uses", "duration_ms")


def _blocks(obj):
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _no_nul(text):
    """Postgres refuses NUL in text; a transcript can carry one as `\\u0000`."""
    return text.replace("\x00", "") if text else text


def _as_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text") or "")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return json.dumps(value, ensure_ascii=False)


def _images(content):
    """The image blocks one tool result carries, by their place inside it.

    Only their position is kept. The bytes stay in the file: a screenshot is
    around 200 KB of base64 and a session can hold hundreds of them.
    """
    if not isinstance(content, list):
        return []
    found = []
    for index, item in enumerate(content):
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        source = item.get("source") or {}
        if source.get("type") == "base64" and source.get("media_type") in IMAGE_TYPES:
            found.append({"media_type": source["media_type"], "index": index})
    return found


def peer_origin(obj):
    """The message another Claude session sent, if this line carries one.

    It arrives two ways: as an attachment when the session was working (the
    CLI queues it into the running turn), or as a user line of its own when
    the session was idle. Both name the sender in an `origin` block. A
    `kind: peer` origin without a message id is a subagent handing its result
    back to its parent, which is not a message between sessions.
    """
    kind = obj.get("type")
    if kind == "user":
        origin, mode = obj.get("origin"), "turn"
    elif kind == "attachment" and (obj.get("attachment") or {}).get("type") == "queued_command":
        origin, mode = obj["attachment"].get("origin"), "queued"
    else:
        return None
    if not isinstance(origin, dict) or origin.get("kind") != "peer" or not origin.get("msg_id"):
        return None
    return {
        "msg_id": origin["msg_id"],
        "from": origin.get("from") or "",
        "name": origin.get("name") or "",
        "body": _no_nul((origin.get("body") or "")[:MAX_TEXT_CHARS]),
        "mode": mode,
    }


def _ts(obj):
    return (obj.get("timestamp") or "").replace("Z", "+00:00") or None


def _peer_row(obj, peer, seq):
    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": obj.get("cwd"),
        "role": "peer",
        "ts": _ts(obj),
        "seq": seq,
        "text": peer["body"] or False,
        "tool_name": False,
        "tool_input": False,
        "tool_use_id": False,
        "peer_name": peer["name"] or False,
        "peer_from": peer["from"] or False,
        "peer_mode": peer["mode"],
        "peer_msg_id": peer["msg_id"],
    }


def parse_line(raw, seq):
    """One JSONL line to a row, plus any tool results it carries.

    Returns `(row_or_None, results)` where `results` maps a tool_use id to
    `{"text": ..., "images": [...]}` for its call. A line that only carries
    results yields no row: the output belongs on the turn that made the call.
    A message from another session is a row of role `peer`.
    """
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None, {}
    if not isinstance(obj, dict):
        return None, {}
    peer = peer_origin(obj)
    if peer:
        return _peer_row(obj, peer, seq), {}
    if obj.get("type") not in CHAT_TYPES:
        return None, {}

    texts, inputs, names, results = [], [], [], {}
    for block in _blocks(obj):
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text") or "")
        elif kind == "tool_use":
            names.append(block.get("name") or "")
            inputs.append(
                json.dumps(block.get("input") or {}, ensure_ascii=False)[:MAX_TOOL_INPUT_CHARS]
            )
        elif kind == "tool_result":
            if block.get("tool_use_id"):
                results[block["tool_use_id"]] = {
                    "text": _no_nul(_as_text(block.get("content"))[:MAX_TOOL_OUTPUT_CHARS]),
                    "images": _images(block.get("content")),
                }

    text = _no_nul("\n".join(t for t in texts if t).strip()[:MAX_TEXT_CHARS])
    tool_input = _no_nul("\n".join(inputs).strip())
    if not text and not tool_input:
        return None, results
    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": obj.get("cwd"),
        "role": obj.get("type"),
        "ts": (obj.get("timestamp") or "").replace("Z", "+00:00") or None,
        "seq": seq,
        "text": text or False,
        "tool_name": ", ".join(n for n in names if n) or False,
        "tool_input": tool_input or False,
        # The first call's id. A turn almost always makes one call, and this is
        # what a later result is matched back to.
        "tool_use_id": next(
            (b["id"] for b in _blocks(obj) if b.get("type") == "tool_use" and b.get("id")),
            False,
        ),
    }, results


def read_tail(path, offset, first_line):
    """Complete lines after `offset`, as `(rows, results, bytes_read, lines_read)`.

    `bytes_read` counts only bytes belonging to newline-terminated lines, so a
    line still being written is read again next time rather than parsed in half.

    Each row and each result also carries `offset`, where its line starts in
    the file. That is how the full content of a call is read back later:
    seeking to a byte is one seek, while finding line N means walking the file.
    """
    rows, results, consumed, lines = [], {}, 0, 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            start = offset + consumed
            consumed += len(raw)
            lines += 1
            row, found = parse_line(raw, first_line + lines)
            if row and row.get("uuid"):
                row["offset"] = start
                rows.append(row)
            for result in found.values():
                result["offset"] = start
            results.update(found)
    return rows, results, consumed, lines


# The most of one turn a reader is shown at once. A turn that ran for hours can
# pass this; the rest stays in the file.
TURN_READ_BYTES = 8 * 1024 * 1024


def read_range(path, start, end):
    """The rows between two byte offsets, each call with its result joined on.

    For showing one turn: nothing is stored, so a row carries no id, and a
    result's images stay in the file.
    """
    rows, results, at = [], {}, start
    with open(path, "rb") as fh:
        fh.seek(start)
        while at < end:
            raw = fh.readline()
            if not raw or not raw.endswith(b"\n"):
                break
            at += len(raw)
            row, found = parse_line(raw, len(rows))
            results.update(found)
            if row:
                rows.append(row)
    out = []
    for row in rows:
        result = results.get(row["tool_use_id"]) if row["tool_use_id"] else None
        out.append({
            "seq": len(out),
            "role": row["role"],
            "ts": row["ts"],
            "text": row["text"] or "",
            "tool_name": row["tool_name"] or "",
            "tool_input": row["tool_input"] or "",
            "tool_output": (result or {}).get("text") or "",
        })
    return out


def _sent(block, obj):
    args = block.get("input") or {}
    body = args.get("message")
    return {
        "kind": "send",
        "tool_use_id": block.get("id"),
        "uuid": obj.get("uuid"),
        "ts": _ts(obj),
        "to": str(args.get("to") or "")[:500],
        "summary": str(args.get("summary") or "")[:500],
        "body": _no_nul((body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
                        [:MAX_TEXT_CHARS]),
    }


def scan_peer_messages(path, offset, limit=None, pending=()):
    """The cross-session messages written after `offset`.

    Returns `(events, consumed, first_ts)`. An event is one of:

    - `send`: a SendMessage call, with what was sent and to whom;
    - `sent`: its answer when it names a message id (a later line);
    - `answered`: its answer when it does not. SendMessage also writes to the
      session's own subagents, and such an answer carries no id; nor does a
      refused send, which is marked `error`;
    - `received`: a message another session sent here.

    `pending` are calls earlier passes saw without their answer yet.

    Only lines that can hold one are parsed: a long session runs to hundreds
    of megabytes and very few of its lines are messages. The tool listings
    carry both marker strings and are the longest lines in a file, so they
    are skipped before parsing. `first_ts` is the first timestamp in the file
    when reading from its start, else None.
    """
    events, consumed, first_ts = [], 0, None
    waiting = set(pending)
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            consumed += len(raw)
            wanted = (b"msg_id" in raw or b'"' + PEER_TOOL.encode() + b'"' in raw
                      or (waiting and b'"tool_result"' in raw
                          and any(call.encode() in raw for call in waiting))) \
                and b'"deferred_tools' not in raw
            if wanted or (offset == 0 and first_ts is None):
                obj = _line(raw)
                if obj is not None:
                    if offset == 0 and first_ts is None and obj.get("timestamp"):
                        first_ts = _ts(obj)
                    if wanted:
                        found = _peer_events(obj, waiting)
                        for event in found:
                            if event["kind"] == "send":
                                waiting.add(event["tool_use_id"])
                            elif event["kind"] in ("sent", "answered"):
                                waiting.discard(event["tool_use_id"])
                        events.extend(found)
            if limit is not None and consumed >= limit:
                break
    return events, consumed, first_ts


def _peer_events(obj, waiting=()):
    peer = peer_origin(obj)
    if peer:
        return [dict(peer, kind="received", uuid=obj.get("uuid"), ts=_ts(obj))]
    kind = obj.get("type")
    if kind == "assistant":
        return [_sent(b, obj) for b in _blocks(obj)
                if b.get("type") == "tool_use" and b.get("name") == PEER_TOOL and b.get("id")]
    if kind != "user":
        return []
    answer = obj.get("toolUseResult")
    results = [b for b in _blocks(obj) if b.get("type") == "tool_result" and b.get("tool_use_id")]
    # `toolUseResult` belongs to the line's result when there is only one, and
    # only the answer to a SendMessage call seen here is one.
    if isinstance(answer, dict) and answer.get("msg_id") and len(results) == 1 \
            and results[0]["tool_use_id"] in waiting:
        return [{"kind": "sent", "tool_use_id": results[0]["tool_use_id"],
                 "msg_id": answer["msg_id"], "ts": _ts(obj)}]
    return [{"kind": "answered", "tool_use_id": b["tool_use_id"], "error": bool(b.get("is_error")),
             "text": _no_nul(_as_text(b.get("content"))[:500]), "ts": _ts(obj)}
            for b in results if b["tool_use_id"] in waiting]


def count_lines(path, upto=None):
    """Newlines in the first `upto` bytes, or the whole file.

    `seq` is the absolute line number in the file, so following a file that
    already has history has to start from the real count, not from how many
    rows happen to be stored.
    """
    total, read = 0, 0
    limit = os.path.getsize(path) if upto is None else upto
    with open(path, "rb") as fh:
        while read < limit:
            chunk = fh.read(min(1 << 20, limit - read))
            if not chunk:
                break
            read += len(chunk)
            total += chunk.count(b"\n")
    return total


def _tag(text, name):
    found = re.search(r"<%s>(.*?)</%s>" % (name, name), text, re.S)
    return found.group(1).strip() if found else ""


def _notification(text):
    """The task notification a line carries, if it carries one."""
    if "<task-notification>" not in text:
        return None
    found = {name: _tag(text, name) for name in NOTIFY_FIELDS}
    for name in USAGE_FIELDS:
        raw = _tag(text, name)
        found[name] = int(raw) if raw.isdigit() else 0
    return found if found.get("tool-use-id") else None


def find_compact_offset(path):
    """Where the last compact boundary line starts, or 0.

    Everything before it left the model's context, so it is also where the
    agent map starts: the same rule the CLI's own reader applies.
    """
    offset, last = 0, 0
    with open(path, "rb") as fh:
        for raw in fh:
            if b'"compact_boundary"' in raw:
                last = offset
            offset += len(raw)
    return last


def _call(block, args, timestamp):
    """What a spawn call says about itself, whichever tool made it."""
    common = {
        "tool_use_id": block.get("id"),
        "ts": (timestamp or "").replace("Z", "+00:00") or None,
    }
    if block.get("name") == WORKFLOW_TOOL:
        script = args.get("script") or ""
        name = WORKFLOW_NAME_RE.search(script)
        common.update({
            "description": (name.group(1) if name else "Workflow")[:200],
            "prompt": script[:MAX_TOOL_INPUT_CHARS],
            "subagent_type": "workflow",
            # A workflow always answers at once with where it will write.
            "is_background": True,
        })
        return common
    common.update({
        "description": (args.get("description") or "")[:200],
        "prompt": (args.get("prompt") or "")[:MAX_TOOL_INPUT_CHARS],
        "subagent_type": args.get("subagent_type") or "",
        "is_background": args.get("run_in_background") is not False,
    })
    return common


def scan_agents(path, offset, known=(), until=None, reset=True):
    """What the lines after `offset` say about agents.

    Returns `(found, boundary, consumed)`. `found` maps a tool_use id to what is
    known about that call so far; a later pass fills in the rest, so nothing
    here needs the whole file. `known` are the calls earlier passes already
    recorded, so a result landing in this pass is matched to its call. `boundary`
    is where a compact boundary was seen, which resets the map, and is None when
    there was none. `reset` off keeps what was found before a boundary, which is
    what reading the whole file rather than the live context asks for.
    """
    found, boundary, consumed = {}, None, 0
    seen = set(known)
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n") or (until is not None and offset + consumed >= until):
                break
            start = offset + consumed
            consumed += len(raw)
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                continue
            kind = obj.get("type")
            if kind == "system" and obj.get("subtype") == "compact_boundary":
                boundary = start
                if reset:
                    found, seen = {}, set()
                continue
            if kind not in CHAT_TYPES:
                continue
            for block in _blocks(obj):
                if block.get("type") == "tool_use" and block.get("name") in AGENT_TOOLS:
                    args = block.get("input") or {}
                    seen.add(block.get("id"))
                    found.setdefault(block.get("id"), {}).update(
                        _call(block, args, obj.get("timestamp")))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in seen:
                    text = _as_text(block.get("content"))
                    launch = LAUNCH_RE.match(text)
                    workflow = WORKFLOW_RE.search(text)
                    entry = found.setdefault(block["tool_use_id"], {
                        "tool_use_id": block["tool_use_id"]})
                    if launch:
                        entry["agent_id"] = launch.group(1)
                    elif workflow:
                        entry["workflow_id"] = workflow.group(1)
                    else:
                        entry["returned"] = True
                elif block.get("type") == "text":
                    notice = _notification(block.get("text") or "")
                    if notice and notice["tool-use-id"] in seen:
                        entry = found.setdefault(notice["tool-use-id"], {
                            "tool_use_id": notice["tool-use-id"]})
                        entry.update({
                            "agent_id": notice["task-id"] or entry.get("agent_id"),
                            "status": notice["status"],
                            "summary": notice["summary"][:MAX_TEXT_CHARS],
                            "output_file": notice["output-file"],
                            "ts_end": (obj.get("timestamp") or "").replace("Z", "+00:00") or None,
                            "total_tokens": notice["subagent_tokens"],
                            "tool_uses": notice["tool_uses"],
                            "duration_ms": notice["duration_ms"],
                        })
    return found, boundary, consumed


def read_usage(path, offset, limit=None):
    """Billed turns after `offset`, as `(rows, bytes_read)`.

    `limit` caps the bytes read in one pass. A session's file can run to
    hundreds of megabytes, and a caller that must finish inside one transaction
    takes it a slice at a time, resuming from the offset it gets back.

    Keyed on the API message id, never on the transcript line. One response is
    written as several lines — thinking, text, tool_use each get their own —
    and every one of them repeats the whole usage block. Counting lines
    multiplies a session's tokens: measured at 1.94x to 2.40x on a long
    session, which is the defect this key exists to avoid.

    The blocks repeat the input side, but `output_tokens` grows as the
    response streams: an early block can say 5 where the last says 145.
    Measured over the transcripts on disk on 2026-09-23, keeping the first
    block counted 74% of the output. So the block with the most output wins,
    and one that is all zero never replaces one that is not.

    Each row also names the prompt it answers: every user line carries the
    `promptId` of the turn it belongs to, tool results included, and an
    assistant line takes the one written before it. A row read before any user
    line in this slice has `prompt_id` None.
    """
    found, consumed, prompt = {}, 0, None
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            consumed += len(raw)
            obj = _line(raw)
            if obj is not None and obj.get("type") == "user" and obj.get("promptId"):
                prompt = obj["promptId"]
            row = _usage_row(obj) if obj is not None else None
            if not row:
                if limit is not None and consumed >= limit:
                    break
                continue
            row["prompt_id"] = prompt
            held = found.get(row["uuid"])
            if (held is None
                    or (not held["total_tokens"] and row["total_tokens"])
                    or (row["total_tokens"] and row["output_tokens"] > held["output_tokens"])):
                found[row["uuid"]] = row
            if limit is not None and consumed >= limit:
                break
    return list(found.values()), consumed


def _line(raw):
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _usage_row(obj):
    if obj.get("type") != "assistant":
        return None
    message = obj.get("message") or {}
    usage = message.get("usage")
    if not isinstance(usage, dict) or not message.get("id"):
        return None
    # The split between the two cache lifetimes is what the price depends on.
    # Older transcripts carry only the total; charging it at the 5 minute rate
    # is the cheaper of the two, so an unknown split never inflates a bill.
    split = usage.get("cache_creation") or {}
    write_5m = split.get("ephemeral_5m_input_tokens")
    write_1h = split.get("ephemeral_1h_input_tokens") or 0
    if write_5m is None and not write_1h:
        write_5m = usage.get("cache_creation_input_tokens") or 0
    write_5m = write_5m or 0
    counts = {
        "input_tokens": usage.get("input_tokens") or 0,
        "cache_read": usage.get("cache_read_input_tokens") or 0,
        "cache_create_5m": write_5m,
        "cache_create_1h": write_1h,
        "output_tokens": usage.get("output_tokens") or 0,
    }
    return dict(
        counts,
        uuid=message["id"],
        session_id=obj.get("sessionId"),
        project=obj.get("cwd"),
        model=message.get("model"),
        ts=(obj.get("timestamp") or "").replace("Z", "+00:00") or None,
        service_tier=usage.get("service_tier"),
        total_tokens=sum(counts.values()),
    )


# Enough of a transcript to find its first prompt without reading a file that
# can run to hundreds of megabytes.
HEAD_BYTES = 512 * 1024
MAX_TITLE_CHARS = 120


# Enough of the end of a transcript to hold the rows the CLI rewrites on every
# turn. They sit last, so a window this size reaches them on any file.
TAIL_BYTES = 256 * 1024


def tail_meta(path, window=TAIL_BYTES):
    """What the end of a transcript says about itself: `(last_ts, title, source)`.

    The CLI writes its own view of a session into the file: a `custom-title`
    row for the name a person gave it, an `ai-title` row for the one it
    generated, and both are rewritten every turn so the last one is the answer.

    `last_ts` is the timestamp of the last real message, which is not the same
    as the file's mtime: the CLI appends `mode`, `last-prompt` and `atis-latch`
    rows after the last message, and the IDE appends one every time the reader
    switches file. Those move the mtime without anything having happened.
    """
    last_ts = title = source = None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - window))
            chunk = fh.read()
    except OSError:
        return None, None, None
    if size > window:
        # The window almost certainly opens mid-line; that half is not JSON.
        chunk = chunk.split(b"\n", 1)[-1]
    for raw in chunk.split(b"\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            continue
        kind = obj.get("type")
        if kind == "custom-title":
            title, source = obj.get("customTitle") or obj.get("title"), "custom"
        elif kind == "ai-title":
            title, source = obj.get("aiTitle"), "ai"
        elif kind in CHAT_TYPES and obj.get("timestamp"):
            last_ts = obj["timestamp"]
    return last_ts, (title or "").strip()[:MAX_TITLE_CHARS] or None, source


def first_meta(path):
    """What a session is, from the head of its file: `(cwd, title)`.

    The title is the first thing the person actually typed. Rows the harness
    injects — a queued notification, a reminder, a slash command's output —
    carry `isMeta` or arrive as an attachment, and naming a session after one
    of those would title half the library the same thing.
    """
    cwd = title = ""
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
    for raw in head.split(b"\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            continue
        cwd = cwd or obj.get("cwd") or ""
        if title or obj.get("type") != "user" or obj.get("isMeta"):
            continue
        text = _as_text((obj.get("message") or {}).get("content"))
        # A slash command is what the person typed too, and its own tag names
        # it better than the expansion that follows.
        named = _tag(text, "command-name")
        text = named or " ".join(re.sub(r"<[^>]+>", " ", text).split())
        if text and not text.startswith("Caveat:"):
            title = text[:MAX_TITLE_CHARS]
    return cwd, title
