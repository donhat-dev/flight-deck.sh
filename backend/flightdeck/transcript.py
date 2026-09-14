"""On-demand reader that turns a Claude Code session JSONL file into a
normalized, chat-renderable transcript.

Read-only and independent of the SQLite ledger: `ingest.py` only stores
*usage* rows (assistant turns that carry a `usage` object), so the actual
conversation text lives nowhere but the raw `.jsonl`. The session-detail view
reads it here, on demand, one file at a time.
"""
import glob
import json
import os
from collections import deque

# Per-block character cap. A single session can be 20+ MB (long tool outputs),
# which would blow up both the JSON response and the browser. We keep the head
# and tail of oversized text with a marker in between.
_MAX_BLOCK_CHARS = 24000
# Hard ceiling on turns returned per request, so a giant session degrades
# gracefully (flagged via `truncated`) instead of returning tens of MB.
_MAX_TURNS = 4000

# JSONL line types that carry conversation content.
_CHAT_TYPES = ("user", "assistant")


def _truncate(s: str, limit: int = _MAX_BLOCK_CHARS) -> str:
    if not isinstance(s, str) or len(s) <= limit:
        return s
    head = limit * 3 // 4
    tail = limit - head
    omitted = len(s) - head - tail
    return f"{s[:head]}\n\n… [{omitted:,} chars truncated] …\n\n{s[-tail:]}"


def find_session_file(projects_dir: str, session_id: str) -> str | None:
    """Locate `<projects_dir>/**/<session_id>.jsonl`.

    The filename always equals the inner `sessionId` (verified across the local
    corpus), so a UUID match is exact. `session_id` is validated as a bare
    token to keep the glob from escaping `projects_dir` (path traversal)."""
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        return None
    projects_dir = os.path.expanduser(projects_dir)
    matches = glob.glob(
        os.path.join(projects_dir, "**", session_id + ".jsonl"), recursive=True)
    return matches[0] if matches else None


def find_subagent_files(projects_dir: str, session_id: str) -> list[str]:
    """Subagent transcripts live NEXT to the session file, in a directory named
    after the session: `<dir>/<session_id>/subagents/agent-*.jsonl`. Their
    records carry the parent sessionId (that's how usage ingest attributes
    them); here we surface them as nested threads in the transcript view.

    Two spawn mechanisms nest at different depths, so we recurse under
    `subagents/`:
      - plain `Agent` tool  → `subagents/agent-*.jsonl`
      - `Workflow` orchestration → `subagents/workflows/<wf_id>/agent-*.jsonl`
    (`**` matches zero-or-more dirs, so both are covered by one glob.)"""
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        return []
    projects_dir = os.path.expanduser(projects_dir)
    return sorted(glob.glob(os.path.join(
        projects_dir, "**", session_id, "subagents", "**", "agent-*.jsonl"),
        recursive=True))


def _flatten_tool_result(content) -> str:
    """tool_result.content is a str, or a list of {type:text|image,...} blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                parts.append(str(b))
            elif b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                parts.append("[image]")
            else:
                parts.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(parts)
    return str(content)


# User prompts that Claude Code injects (not typed by the human) are wrapped in
# these tags; the caveat marker is a literal prefix. Flagging them lets the UI
# dim/collapse machine-generated content while keeping real prompts prominent.
_META_PREFIXES = (
    "<local-command-caveat>", "<command-name>", "<command-message>",
    "<command-args>", "<command-stdout>", "<command-stderr>",
    "<system-reminder>", "<ide_opened_file>", "<ide_selection>",
    "<user-prompt-submit-hook>", "Caveat:",
)


def _is_meta_text(s: str) -> bool:
    return isinstance(s, str) and any(s.lstrip().startswith(p) for p in _META_PREFIXES)


def _norm_block(b) -> dict | None:
    """Normalize one content block into a compact, render-ready shape."""
    if not isinstance(b, dict):
        return None
    t = b.get("type")
    if t == "text":
        txt = b.get("text", "")
        return {"type": "text", "text": _truncate(txt), "meta": _is_meta_text(txt)}
    if t == "thinking":
        return {"type": "thinking", "text": _truncate(b.get("thinking", ""))}
    if t == "tool_use":
        # Truncate long string inputs (e.g. a Write's whole file body) so a
        # single call can't bloat the response.
        raw_input = b.get("input") or {}
        clean_input = {
            k: (_truncate(v) if isinstance(v, str) else v)
            for k, v in raw_input.items()
        } if isinstance(raw_input, dict) else raw_input
        return {
            "type": "tool_use",
            "id": b.get("id"),
            "name": b.get("name"),
            "input": clean_input,
        }
    if t == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": b.get("tool_use_id"),
            "is_error": bool(b.get("is_error")),
            "content": _truncate(_flatten_tool_result(b.get("content"))),
        }
    if t == "image":
        return {"type": "image"}
    return None


# The block types `_norm_block` knows how to render. Kept next to it so the
# cheap counter below cannot drift from the real normalizer.
_RENDERABLE_BLOCKS = ("text", "thinking", "tool_use", "tool_result", "image")


def _has_renderable(obj: dict) -> bool:
    """Would `_norm_turn` keep this line? Answers it without building or
    truncating a single block — for counting only, where the turns themselves
    are never returned."""
    raw = (obj.get("message") or {}).get("content")
    if isinstance(raw, str):
        return bool(raw.strip())
    if isinstance(raw, list):
        return any(isinstance(b, dict) and b.get("type") in _RENDERABLE_BLOCKS
                   for b in raw)
    return False


def _norm_turn(obj: dict) -> dict | None:
    """Turn one `user`/`assistant` JSONL line into a chat turn, or None if it
    carries nothing renderable."""
    role = obj.get("type")
    msg = obj.get("message") or {}
    raw = msg.get("content")

    blocks: list[dict] = []
    text_meta = False
    if isinstance(raw, str):
        text_meta = _is_meta_text(raw)
        if raw.strip():
            blocks.append({"type": "text", "text": _truncate(raw), "meta": text_meta})
    elif isinstance(raw, list):
        for b in raw:
            nb = _norm_block(b)
            if nb is not None:
                blocks.append(nb)

    if not blocks:
        return None

    # Turn is "meta" only if EVERY renderable block is machine-injected, so a
    # real prompt bundled with an <ide_opened_file> block still reads as a user turn.
    all_meta = all(b.get("meta") for b in blocks if b["type"] == "text") and \
        all(b["type"] == "text" for b in blocks)

    return {
        "role": role,
        "ts": obj.get("timestamp"),
        "uuid": obj.get("uuid"),
        "is_meta": bool(obj.get("isMeta")) or (text_meta if isinstance(raw, str) else all_meta),
        "is_sidechain": bool(obj.get("isSidechain")),
        "blocks": blocks,
    }


# ---- turn index --------------------------------------------------------
# Rebuilding a transcript window used to re-parse the whole file: 800ms on a
# 130 MB session, paid again on every live-follow tick. Instead we keep a byte
# offset per renderable turn, cached per file and EXTENDED in place as the file
# grows — a session .jsonl is append-only, so a tick costs only the new bytes.
_INDEX_CACHE: dict[str, dict] = {}
_INDEX_CACHE_MAX = 64


def _blank_index() -> dict:
    return {"offsets": [], "title": None, "title_is_custom": False,
            "project": None, "git_branch": None, "version": None,
            "agent_type": None, "first_ts": None, "last_ts": None,
            "pos": 0, "size": 0, "mtime_ns": 0}


def _scan_index(path: str, idx: dict) -> None:
    """Read everything after `idx['pos']` and fold it into the index.

    Offsets are BYTE offsets, so the file is read in binary; a trailing line
    without its newline is a writer mid-append and is left for the next pass."""
    with open(path, "rb") as fh:
        fh.seek(idx["pos"])
        while True:
            start = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                break                      # incomplete tail line: retry later
            idx["pos"] = fh.tell()
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            typ = obj.get("type")
            if typ == "custom-title" and obj.get("customTitle"):
                idx["title"] = obj["customTitle"]
                idx["title_is_custom"] = True
                continue
            if typ == "ai-title" and obj.get("aiTitle") and not idx["title_is_custom"]:
                idx["title"] = obj["aiTitle"]
                continue
            if typ == "summary" and obj.get("summary") and not idx["title"]:
                idx["title"] = obj["summary"]
                continue
            if typ not in _CHAT_TYPES:
                continue
            for key, src in (("project", "cwd"), ("git_branch", "gitBranch"),
                             ("version", "version"), ("agent_type", "attributionAgent")):
                if idx[key] is None and obj.get(src):
                    idx[key] = obj[src]
            if not _has_renderable(obj):
                continue
            ts = obj.get("timestamp")
            if ts:
                if idx["first_ts"] is None or ts < idx["first_ts"]:
                    idx["first_ts"] = ts
                if idx["last_ts"] is None or ts > idx["last_ts"]:
                    idx["last_ts"] = ts
            idx["offsets"].append(start)


def _index(path: str) -> dict:
    """The cached index for `path`, extended or rebuilt as the file demands."""
    st = os.stat(path)
    hit = _INDEX_CACHE.get(path)
    if hit is not None and hit["mtime_ns"] == st.st_mtime_ns and hit["size"] == st.st_size:
        return hit
    # Growth is an append; anything else (truncated, rewritten) means rebuild.
    idx = hit if (hit is not None and st.st_size >= hit["size"]) else _blank_index()
    _scan_index(path, idx)
    idx["mtime_ns"], idx["size"] = st.st_mtime_ns, st.st_size
    if path not in _INDEX_CACHE and len(_INDEX_CACHE) >= _INDEX_CACHE_MAX:
        _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
    _INDEX_CACHE[path] = idx
    return idx


def _read_turns(path: str, offsets: list[int], start: int, count: int) -> list[dict]:
    """Normalize just the turns in [start, start+count) by seeking to each."""
    turns: list[dict] = []
    if count <= 0:
        return turns
    with open(path, "rb") as fh:
        for i in range(start, min(start + count, len(offsets))):
            fh.seek(offsets[i])
            raw = fh.readline()
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            turn = _norm_turn(obj)
            if turn is not None:
                turns.append(turn)
    return turns


def build_transcript(path: str, offset: int = 0, limit: int = _MAX_TURNS,
                     anchor: str = "head") -> dict:
    """Parse a session `.jsonl` into an ordered, normalized transcript.

    Only the requested window is normalized — the rest of the file is touched
    once, to index it, and never again while it is unchanged.

    `anchor="tail"` ignores `offset` and returns the LAST `limit` turns: what a
    reader opening a 38k-turn session actually wants, and the only way to reach
    its end without shipping everything before it."""
    limit = max(0, min(limit, _MAX_TURNS))
    idx = _index(path)
    total = len(idx["offsets"])
    win_offset = max(0, total - limit) if anchor == "tail" else min(max(0, offset), total)
    turns = _read_turns(path, idx["offsets"], win_offset, limit)

    return {
        "title": idx["title"],
        "project": idx["project"],
        "git_branch": idx["git_branch"],
        "version": idx["version"],
        "agent_type": idx["agent_type"],
        "first_ts": idx["first_ts"],
        "last_ts": idx["last_ts"],
        "turn_count": total,
        "offset": win_offset,
        "returned": len(turns),
        # `truncated` = the window is not the whole session. `has_before` /
        # `has_after` tell the reader WHICH end is missing — what a
        # scroll-to-load client needs to decide which way to fetch.
        "truncated": total > len(turns),
        "has_before": win_offset > 0,
        "has_after": win_offset + len(turns) < total,
        "turns": turns,
    }


# Per-subagent turn cap: nested threads render collapsed, but a runaway
# subagent (hundreds of grep turns) must not blow up the parent payload.
_MAX_SUBAGENT_TURNS = 600


def _dispatch_text(turns) -> str:
    """First real user text of a subagent transcript == the Agent tool's
    dispatch prompt. Used by the UI to nest the thread under the right call."""
    for t in turns:
        if t.get("role") != "user":
            continue
        for b in t.get("blocks", []):
            if b.get("type") == "text" and not b.get("meta") and b.get("text", "").strip():
                return b["text"][:300]
    return ""


def subagent_files_by_session(projects_dir: str) -> dict[str, list[str]]:
    """Every subagent transcript in the tree, grouped by parent session, in ONE
    tree walk.

    `find_subagent_files` runs a recursive `**` glob per session, so the sessions
    list paid for 100 walks of the whole tree — 202ms measured, against 3ms for a
    single walk that finds all 285 files. Four snapshot ranges made that 808ms
    per rebuild, for a result that is identical every time.

    The session id is the directory two levels above `subagents/`, which is the
    same layout `find_subagent_files` encodes; workflow agents nest deeper under
    `subagents/workflows/<wf_id>/` and still resolve to the same parent.
    """
    root = os.path.expanduser(projects_dir)
    out: dict[str, list[str]] = {}
    for path in glob.glob(os.path.join(root, "**", "subagents", "**",
                                       "agent-*.jsonl"), recursive=True):
        # .../<session_id>/subagents/[workflows/<wf>/]agent-x.jsonl
        parts = path.split(os.sep)
        try:
            sid = parts[parts.index("subagents") - 1]
        except (ValueError, IndexError):
            continue
        out.setdefault(sid, []).append(path)
    for paths in out.values():
        paths.sort()
    return out


# Parsed subagent files, keyed by path and invalidated by (mtime_ns, size). A
# finished agent's transcript never changes again, so re-reading it on every
# snapshot was 173ms of pure repetition per range. Bounded by the number of agent
# files on disk, and an entry is replaced rather than added when a file changes.
_SUBAGENT_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}


def _parse_subagent_file(path: str) -> dict | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _SUBAGENT_CACHE.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    parsed = _read_subagent_file(path)
    if parsed is not None:
        _SUBAGENT_CACHE[path] = (stamp, parsed)
    return parsed


def _read_subagent_file(path: str) -> dict | None:
    """Parse one subagent transcript. None when it cannot be opened."""
    agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
    agent_type = model = first_ts = last_ts = dispatch = None
    turns = 0
    renderable = 0   # chat turns the detail view would draw (user + assistant)
    comp = {"input_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 0}
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if agent_type is None and obj.get("attributionAgent"):
                agent_type = obj["attributionAgent"]
            ts = obj.get("timestamp")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
            if obj.get("type") in _CHAT_TYPES and _has_renderable(obj):
                renderable += 1
            if obj.get("type") != "assistant":
                if dispatch is None and obj.get("type") == "user":
                    raw = (obj.get("message") or {}).get("content")
                    if isinstance(raw, str):
                        txt = raw
                    elif isinstance(raw, list):
                        txt = " ".join(
                            b.get("text", "") for b in raw
                            if isinstance(b, dict) and b.get("type") == "text")
                    else:
                        txt = ""
                    if txt and not _is_meta_text(txt):
                        dispatch = txt[:300]
                continue
            turns += 1
            msg = obj.get("message") or {}
            model = msg.get("model") or model
            u = msg.get("usage") or {}
            for k in comp:
                comp[k] += u.get(k, 0)
    return {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "model": model,
        "turns": turns,
        "renderable_turns": renderable,
        "input_tokens": comp["input_tokens"],
        "cache_read": comp["cache_read_input_tokens"],
        "cache_create_5m": comp["cache_creation_input_tokens"],
        "cache_create_1h": 0,
        "output": comp["output_tokens"],
        "first_ts": first_ts,
        "last_ts": last_ts,
        "dispatch": dispatch or "",
    }


def subagent_usage(projects_dir: str, session_id: str,
                   files: list[str] | None = None) -> list[dict]:
    """Lightweight per-subagent stats for the sessions LIST (no full turn
    normalization): usage components, turn count, agent type, dispatch prompt.
    Cost is computed by the caller (metrics) so this stays pricing-free.

    These rows are a *breakdown* of the parent — the ledger already attributes
    subagent usage to the parent session_id, so the parent's totals include them.

    `files` lets a caller that already walked the tree (see
    `subagent_files_by_session`) skip this session's own recursive glob. Passing
    an empty list means "this session has none", which is why the default is None
    rather than [].
    """
    paths = find_subagent_files(projects_dir, session_id) if files is None else files
    out = []
    for sp in paths:
        parsed = _parse_subagent_file(sp)
        if parsed is not None:
            out.append(parsed)
    return out


def _agent_id_of(path: str) -> str:
    return os.path.basename(path)[len("agent-"):-len(".jsonl")]


def _subagent_thread(path: str, limit: int = _MAX_SUBAGENT_TURNS) -> dict | None:
    """One subagent transcript, turns included — the shape the UI renders when
    a nested thread is expanded."""
    try:
        sub = build_transcript(path, offset=0, limit=limit)
    except OSError:
        return None
    turns = sub.get("turns", [])
    return {
        "agent_id": _agent_id_of(path),
        "agent_type": sub.get("agent_type"),
        "turn_count": sub.get("turn_count"),
        "truncated": sub.get("truncated"),
        "first_ts": sub.get("first_ts"),
        "last_ts": sub.get("last_ts"),
        "dispatch": _dispatch_text(turns),
        "turns": turns,
        "loaded": True,
    }


def load_session(projects_dir: str, session_id: str, offset: int = 0,
                 limit: int = _MAX_TURNS, anchor: str = "head",
                 subagent_turns: bool = False) -> dict | None:
    """Resolve + parse a session by id (plus its nested subagent transcripts).
    None if no matching file exists.

    Subagent threads ship as METADATA by default. On a real session that is the
    whole difference between a 15 MB response and a 4 MB one: 65 nested threads
    carried 10 MB of turns that render collapsed and are usually never opened.
    They are fetched one at a time on expand (`load_subagent`); pass
    `subagent_turns=True` for the old all-in-one payload."""
    path = find_session_file(projects_dir, session_id)
    if not path:
        return None
    data = build_transcript(path, offset=offset, limit=limit, anchor=anchor)
    data["session_id"] = session_id

    subs = []
    for sp in find_subagent_files(projects_dir, session_id):
        if subagent_turns:
            thread = _subagent_thread(sp)
            if thread is not None:
                subs.append(thread)
            continue
        # Lean path: the mtime-cached usage parser already carries every field
        # the collapsed row shows, and never materializes a turn.
        meta = _parse_subagent_file(sp)
        if meta is None:
            continue
        subs.append({
            "agent_id": meta["agent_id"],
            "agent_type": meta["agent_type"],
            "turn_count": meta.get("renderable_turns", meta["turns"]),
            "truncated": False,
            "first_ts": meta["first_ts"],
            "last_ts": meta["last_ts"],
            "dispatch": meta["dispatch"],
            "turns": None,     # fetched on expand
            "loaded": False,
        })
    data["subagents"] = subs
    return data


def load_subagent(projects_dir: str, session_id: str, agent_id: str,
                  limit: int = _MAX_SUBAGENT_TURNS) -> dict | None:
    """One nested thread of a session, by agent id. None if it does not exist."""
    for sp in find_subagent_files(projects_dir, session_id):
        if _agent_id_of(sp) == agent_id:
            return _subagent_thread(sp, limit=limit)
    return None
