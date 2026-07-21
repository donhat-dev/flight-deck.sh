"""Incremental parser/loader for Claude Code session JSONL files."""
import glob
import json
import os

_COLS = ("uuid", "session_id", "project", "model", "ts", "input_tokens",
         "cache_read", "cache_create_5m", "cache_create_1h",
         "output_tokens", "service_tier")

_TOOL_COLS = ("id", "session_id", "project", "ts", "tool", "server", "detail")

# Per-process fast-skip cache: path -> (mtime, size) of the last ingest. Lets a
# reingest sweep skip an unchanged file with a single os.stat (no open, no DB
# hit), which is what makes the periodic full sweep cheap over a slow (virtiofs)
# mount with hundreds of session files.
_stat_cache: dict = {}


def parse_line(obj: dict) -> dict | None:
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None
    cc = usage.get("cache_creation") or {}
    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": obj.get("cwd"),
        "model": msg.get("model"),
        "ts": obj.get("timestamp"),
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_create_5m": cc.get("ephemeral_5m_input_tokens", 0),
        "cache_create_1h": cc.get("ephemeral_1h_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "service_tier": usage.get("service_tier"),
    }


def _split_mcp(name: str):
    """mcp__<server>__<tool> -> ("<server>", "<tool>"). The server slug is the
    segment between the first and second "__"; everything after the second
    "__" is the tool short name (tool names may themselves contain "__")."""
    rest = name[len("mcp__"):]
    server, sep, detail = rest.partition("__")
    if not sep:
        # malformed (no second "__"): treat whole tail as server, no detail
        return server or None, None
    return (server or None), (detail or None)


def tool_rows(obj: dict) -> list:
    """Extract one tool_calls row per tool_use content block of an assistant
    message. Runs independently of usage (a tool_use line normally carries
    usage, but we never gate on it here). Row shape matches _TOOL_COLS.

    id = "<message uuid>:<content block index>" (index is the block's position
    in the message content array, so parallel tool calls get distinct ids)."""
    if obj.get("type") != "assistant":
        return []
    uuid = obj.get("uuid")
    if not uuid:
        return []
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    session_id = obj.get("sessionId")
    project = obj.get("cwd")
    ts = obj.get("timestamp")
    rows = []
    for idx, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool = block.get("name")
        if not tool:
            continue
        server = detail = None
        if tool.startswith("mcp__"):
            server, detail = _split_mcp(tool)
        elif tool == "Skill":
            detail = (block.get("input") or {}).get("skill")
        rows.append({
            "id": f"{uuid}:{idx}",
            "session_id": session_id,
            "project": project,
            "ts": ts,
            "tool": tool,
            "server": server,
            "detail": detail,
        })
    return rows


def _insert_tool_calls(conn, obj: dict) -> int:
    rows = tool_rows(obj)
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in _TOOL_COLS)
    sql = (f"INSERT INTO tool_calls ({','.join(_TOOL_COLS)}) "
           f"VALUES ({placeholders}) ON CONFLICT(id) DO NOTHING")
    n = 0
    for row in rows:
        cur = conn.execute(sql, tuple(row[c] for c in _TOOL_COLS))
        n += cur.rowcount
    return n


def _upsert(conn, row: dict) -> bool:
    if not row.get("uuid"):
        return False
    placeholders = ",".join("?" for _ in _COLS)
    sql = (f"INSERT INTO messages ({','.join(_COLS)}) VALUES ({placeholders}) "
           "ON CONFLICT(uuid) DO NOTHING")
    cur = conn.execute(sql, tuple(row[c] for c in _COLS))
    return cur.rowcount > 0


def _title_upsert(conn, obj: dict) -> None:
    """Capture session titles into session_meta.

    Two sources: 'ai-title' (auto-generated) and 'custom-title' (user rename
    or a fork's "Forked: …" label). A user-set custom title must WIN over the
    auto title regardless of record order, so ai-title never overwrites a row
    already marked 'custom'."""
    typ = obj.get("type")
    if typ == "custom-title":
        sid, title = obj.get("sessionId"), obj.get("customTitle")
        if not sid or not title:
            return
        conn.execute(
            "INSERT INTO session_meta (session_id, title, title_source) "
            "VALUES (?, ?, 'custom') ON CONFLICT(session_id) DO UPDATE SET "
            "title=excluded.title, title_source='custom'",
            (sid, title))
    elif typ == "ai-title":
        sid, title = obj.get("sessionId"), obj.get("aiTitle")
        if not sid or not title:
            return
        conn.execute(
            "INSERT INTO session_meta (session_id, title, title_source) "
            "VALUES (?, ?, 'ai') ON CONFLICT(session_id) DO UPDATE SET "
            "title=excluded.title, title_source='ai' "
            "WHERE session_meta.title_source IS DISTINCT FROM 'custom'",
            (sid, title))


def ingest_file(conn, path: str) -> int:
    try:
        st = os.stat(path)
    except OSError:
        return 0
    # Fast skip: unchanged since our last ingest (same mtime+size) -> nothing to
    # do, and we avoid opening the file or touching the DB entirely.
    key = (st.st_mtime, st.st_size)
    if _stat_cache.get(path) == key:
        return 0
    rec = conn.execute("SELECT bytes_ingested, size FROM files WHERE path=?",
                       (path,)).fetchone()
    start = 0
    if rec and st.st_size >= rec["size"]:
        start = rec["bytes_ingested"]
    # else: new file or truncated/rotated -> re-ingest from 0
    new_rows = 0
    lines = 0
    committed = 0  # bytes past the last newline-terminated line consumed
    with open(path, "rb") as fh:
        fh.seek(start)
        for raw in fh:
            # A trailing line without "\n" is incomplete (session still
            # writing): leave its bytes for the next run and stop.
            if not raw.endswith(b"\n"):
                break
            committed += len(raw)
            lines += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            row = parse_line(obj)
            if row and _upsert(conn, row):
                new_rows += 1
            else:
                _title_upsert(conn, obj)
            # tool_use extraction is independent of usage/message dedup: an
            # assistant line carries BOTH a usage row and its tool_use block,
            # so run it for every line (INSERT OR IGNORE keeps it idempotent).
            _insert_tool_calls(conn, obj)
    conn.execute(
        "INSERT INTO files (path, mtime, size, bytes_ingested, lines_ingested) "
        "VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
        "mtime=excluded.mtime, size=excluded.size, "
        "bytes_ingested=excluded.bytes_ingested, "
        "lines_ingested=files.lines_ingested+excluded.lines_ingested",
        (path, st.st_mtime, st.st_size, start + committed, lines))
    conn.commit()
    _stat_cache[path] = key   # mark clean so the next sweep skips it
    return new_rows


def _maybe_backfill_tool_calls(conn) -> bool:
    """One-time migration: tool_calls was added after messages were already
    ingested, so the `files` gate would keep those files from ever re-yielding
    tool_use rows. If tool_calls is empty while messages is non-empty, clear the
    `files` table (and the per-process stat cache) to force a full re-scan;
    the messages PK dedupes, tool_calls uses INSERT OR IGNORE. Cheap: two EXISTS
    probes, and it self-disables once tool_calls has any row."""
    has_tools = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM tool_calls)").fetchone()[0]
    if has_tools:
        return False
    has_msgs = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM messages)").fetchone()[0]
    if not has_msgs:
        return False
    conn.execute("DELETE FROM files")
    conn.commit()
    _stat_cache.clear()
    print("[ingest] tool_calls backfill: cleared files table to re-scan for "
          "tool_use blocks (messages PK dedupes)")
    return True


def ingest_all(conn, projects_dir: str) -> int:
    _maybe_backfill_tool_calls(conn)
    projects_dir = os.path.expanduser(projects_dir)
    total = 0
    for path in glob.glob(os.path.join(projects_dir, "**", "*.jsonl"),
                          recursive=True):
        total += ingest_file(conn, path)
    return total


def ingest_paths(conn, paths) -> int:
    """Ingest a specific set of changed files (watcher delta) instead of
    re-globbing the whole tree. Non-jsonl / missing paths are skipped."""
    _maybe_backfill_tool_calls(conn)
    total = 0
    for path in paths:
        if str(path).endswith(".jsonl"):
            total += ingest_file(conn, path)
    return total


if __name__ == "__main__":
    from flightdeck import config, db

    cfg = config.load(os.environ.get("TOKEN_AUDIT_CONFIG", "config.toml"))
    conn = db.connect(cfg["db_path"])
    n = ingest_all(conn, cfg["projects_dir"])
    print(f"Ingested {n} new rows")
