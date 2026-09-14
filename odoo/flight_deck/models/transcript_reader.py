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

CHAT_TYPES = ("user", "assistant")
MAX_TEXT_CHARS = 24000
MAX_TOOL_INPUT_CHARS = 4000
MAX_TOOL_OUTPUT_CHARS = 8000
# What a browser will render as an image without being able to run anything.
# SVG is deliberately absent: served same-origin it is a script.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def _blocks(obj):
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


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


def parse_line(raw, seq):
    """One JSONL line to a row, plus any tool results it carries.

    Returns `(row_or_None, results)` where `results` maps a tool_use id to
    `{"text": ..., "images": [...]}` for its call. A line that only carries
    results yields no row: the output belongs on the turn that made the call.
    """
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None, {}
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
                    "text": _as_text(block.get("content"))[:MAX_TOOL_OUTPUT_CHARS],
                    "images": _images(block.get("content")),
                }

    text = "\n".join(t for t in texts if t).strip()[:MAX_TEXT_CHARS]
    tool_input = "\n".join(inputs).strip()
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

    Each result also carries `offset`, where its line starts in the file. That
    is how an image is read back later: seeking to a byte is one seek, while
    finding line N means walking the file.
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
                rows.append(row)
            for result in found.values():
                result["offset"] = start
            results.update(found)
    return rows, results, consumed, lines


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
