"""SQLite store for Claude Code token-usage messages (raw counts only).

Connection creation lives here on purpose: every read path goes through
`open_read` / `read_conn`, so migrating the engine (SQLite -> PostgreSQL) is a
change to THIS file, not a sweep across every call site. Keep the dialect
details (row factory, busy-timeout) behind these helpers.
"""
import contextlib
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  uuid TEXT PRIMARY KEY,
  session_id TEXT,
  project TEXT,
  model TEXT,
  ts TEXT,
  input_tokens INTEGER,
  cache_read INTEGER,
  cache_create_5m INTEGER,
  cache_create_1h INTEGER,
  output_tokens INTEGER,
  service_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_model ON messages(model);

CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  mtime REAL,
  size INTEGER,
  bytes_ingested INTEGER,
  lines_ingested INTEGER
);

CREATE TABLE IF NOT EXISTS session_meta (
  session_id TEXT PRIMARY KEY,
  title TEXT,
  title_source TEXT   -- 'custom' (user rename / fork) wins over 'ai' (auto)
);

-- One row per tool_use block in an assistant message. Powers the Systems
-- views (Comms/Manuals): which MCP servers and skills are actually used.
CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,   -- "<message uuid>:<content block index>"
  session_id TEXT,
  project TEXT,
  ts TEXT,
  tool TEXT,             -- raw tool_use name (Bash, Skill, mcp__<server>__<tool>, ...)
  server TEXT,           -- MCP server slug for mcp__* tools, else NULL
  detail TEXT            -- Skill: skill arg; mcp__*: tool short name; else NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS idx_tool_calls_server ON tool_calls(server);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # migrate pre-existing session_meta tables that lack title_source
    cols = {r[1] for r in conn.execute("PRAGMA table_info(session_meta)")}
    if "title_source" not in cols:
        conn.execute("ALTER TABLE session_meta ADD COLUMN title_source TEXT")
    conn.commit()
    return conn


def open_read(db_path: str) -> sqlite3.Connection:
    """A short-lived READ connection, configured exactly like the per-request
    readers (row factory + bounded busy-timeout). One thread owns each; the
    caller must close it (or use `read_conn`). This is the single seam the
    Postgres migration replaces."""
    c = sqlite3.connect(db_path, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    return c


@contextlib.contextmanager
def read_conn(db_path: str):
    """Context-managed read connection (preferred over open_read): closes on
    exit, so call sites stop hand-writing try/finally."""
    c = open_read(db_path)
    try:
        yield c
    finally:
        c.close()
