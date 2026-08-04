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


def build_transcript(path: str, offset: int = 0, limit: int = _MAX_TURNS) -> dict:
    """Parse a session `.jsonl` into an ordered, normalized transcript.

    Streams the file line by line; captures the ai-title / cwd / gitBranch /
    version for a header, and every renderable user/assistant turn. Turns are
    windowed by (offset, limit) with `_MAX_TURNS` as a hard cap."""
    limit = max(0, min(limit, _MAX_TURNS))
    title = None
    title_is_custom = False  # user rename / fork label — wins over ai-title/summary
    project = git_branch = version = None
    agent_type = None   # subagent transcripts carry attributionAgent (e.g. "Explore")
    first_ts = last_ts = None
    turns: list[dict] = []
    total = 0  # renderable turns seen (pre-window)

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            typ = obj.get("type")
            # user-set custom title (rename) or a fork's "Forked: …" label —
            # highest priority, and once seen an ai-title can't override it.
            if typ == "custom-title" and obj.get("customTitle"):
                title = obj["customTitle"]
                title_is_custom = True
                continue
            if typ == "ai-title" and obj.get("aiTitle") and not title_is_custom:
                title = obj["aiTitle"]
                continue
            if typ == "summary" and obj.get("summary") and not title:
                title = obj["summary"]
                continue
            if typ not in _CHAT_TYPES:
                continue

            # header fields: first non-empty wins (they're stable per session)
            if project is None:
                project = obj.get("cwd")
            if git_branch is None:
                git_branch = obj.get("gitBranch")
            if version is None:
                version = obj.get("version")
            if agent_type is None and obj.get("attributionAgent"):
                agent_type = obj.get("attributionAgent")

            turn = _norm_turn(obj)
            if turn is None:
                continue
            ts = turn.get("ts")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

            # window: keep only [offset, offset+limit)
            if offset <= total < offset + limit:
                turns.append(turn)
            total += 1

    return {
        "title": title,
        "project": project,
        "git_branch": git_branch,
        "version": version,
        "agent_type": agent_type,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "turn_count": total,
        "offset": offset,
        "returned": len(turns),
        "truncated": (offset + len(turns)) < total,
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


def load_session(projects_dir: str, session_id: str, offset: int = 0,
                 limit: int = _MAX_TURNS) -> dict | None:
    """Resolve + parse a session by id (plus its nested subagent transcripts).
    None if no matching file exists."""
    path = find_session_file(projects_dir, session_id)
    if not path:
        return None
    data = build_transcript(path, offset=offset, limit=limit)
    data["session_id"] = session_id

    subs = []
    for sp in find_subagent_files(projects_dir, session_id):
        try:
            sub = build_transcript(sp, offset=0, limit=_MAX_SUBAGENT_TURNS)
        except OSError:
            continue
        agent_id = os.path.basename(sp)[len("agent-"):-len(".jsonl")]
        subs.append({
            "agent_id": agent_id,
            "agent_type": sub.get("agent_type"),
            "turn_count": sub.get("turn_count"),
            "truncated": sub.get("truncated"),
            "first_ts": sub.get("first_ts"),
            "last_ts": sub.get("last_ts"),
            "dispatch": _dispatch_text(sub.get("turns", [])),
            "turns": sub.get("turns", []),
        })
    data["subagents"] = subs
    return data
