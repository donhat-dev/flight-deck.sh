"""Storage layer — SQLite by default, PostgreSQL when a database_url is set.

Every connection is created here so switching engines is a change to THIS file,
not a sweep across call sites. Call sites keep the sqlite3 idiom
(`conn.execute(sql_with_?_placeholders, params).fetchall()`, rows by key,
`.commit()/.close()`); on PostgreSQL a thin wrapper makes a psycopg3 connection
quack the same way (translating `?`->`%s`, dict rows, pool checkout on close).

Engine is chosen by `configure(cfg)` at startup: cfg["database_url"] set =>
PostgreSQL, else SQLite at cfg["db_path"].
"""
import contextlib
import sqlite3

# --- engine state (set by configure) ----------------------------------------
_URL = None          # PostgreSQL DSN, or None for SQLite
_POOL = None         # psycopg_pool.ConnectionPool for reads, or None


def configure(cfg: dict) -> None:
    """Pick the engine from cfg once, at app startup. Idempotent."""
    global _URL, _POOL
    _URL = cfg.get("database_url") or None
    if _URL and _POOL is None:
        from psycopg_pool import ConnectionPool
        # Reads are short + autocommit (no lingering transaction holding rows).
        _POOL = ConnectionPool(
            _URL, min_size=1, max_size=8, open=True,
            kwargs={"row_factory": _hybrid_row, "autocommit": True})


# --- row factory: rows support BOTH r[i] and r["col"] (like sqlite3.Row) -----
# Call sites mix positional (`row[0]` on a scalar SELECT) and key access; a
# hybrid row means neither engine forces a call-site rewrite.
class _Row:
    __slots__ = ("_cols", "_vals", "_map")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals
        self._map = None

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        if self._map is None:
            self._map = dict(zip(self._cols, self._vals))
        return self._map[k]

    def keys(self):
        return list(self._cols)

    def get(self, k, default=None):
        try:
            return self[k]
        except (KeyError, IndexError):
            return default

    def __iter__(self):
        return iter(self._vals)


def _hybrid_row(cursor):
    cols = [d.name for d in (cursor.description or [])]
    return lambda vals: _Row(cols, vals)


def is_postgres() -> bool:
    return _URL is not None


# --- psycopg adapter: make a psycopg3 connection look like sqlite3 -----------
class _PgConn:
    """Wrap a psycopg connection so existing sqlite-style call sites work:
    `?` placeholders are translated to psycopg's `%s`, rows come back as dicts
    (key access), and `.close()` returns a pooled connection to the pool."""

    def __init__(self, conn, pool=None):
        self._c = conn
        self._pool = pool

    def execute(self, sql, params=()):
        # qmark -> pyformat. Our SQL never contains a literal '?'.
        return self._c.execute(sql.replace("?", "%s"), params or ())

    def executescript(self, script):
        # sqlite3 API used for schema bootstrap; psycopg runs multi-statement
        # scripts in one execute (no params involved).
        self._c.execute(script)
        return self._c

    def commit(self):
        self._c.commit()

    def close(self):
        if self._pool is not None:
            self._pool.putconn(self._c)
        else:
            self._c.close()

    # `with open_read(...) as conn:` MUST route to close() above, i.e. return the
    # connection to the pool. Without these, `__getattr__` below hands `with` to
    # the wrapped psycopg connection's own context manager, which commits and
    # leaves the connection checked out forever — a silent leak that only shows
    # up when the pool is exhausted and every request starts timing out. That
    # happened: eight debounced watcher pings killed the whole app.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._c, name)


# --- schemas -----------------------------------------------------------------
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

-- App settings that must outlive a browser profile. Appearance lives here rather
-- than in localStorage so the choice is the SYSTEM's, not one browser's.
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  project TEXT,
  ts TEXT,
  tool TEXT,
  server TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS idx_tool_calls_server ON tool_calls(server);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts);
"""

# PostgreSQL schema. messages/tool_calls/files are DERIVED from the JSONL and
# fully rebuildable, so they are UNLOGGED (skip WAL, faster ingest, emptied only
# on an unclean crash -> re-ingested on next start). session_meta holds user
# input (custom titles) so it stays LOGGED (durable). hub_credentials is created
# LOGGED by hub/credentials.py.
_SCHEMA_PG = """
CREATE UNLOGGED TABLE IF NOT EXISTS messages (
  uuid text PRIMARY KEY,
  session_id text,
  project text,
  model text,
  ts text,
  input_tokens bigint,
  cache_read bigint,
  cache_create_5m bigint,
  cache_create_1h bigint,
  output_tokens bigint,
  service_tier text
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_model ON messages(model);

CREATE UNLOGGED TABLE IF NOT EXISTS files (
  path text PRIMARY KEY,
  mtime double precision,
  size bigint,
  bytes_ingested bigint,
  lines_ingested bigint
);

CREATE TABLE IF NOT EXISTS session_meta (
  session_id text PRIMARY KEY,
  title text,
  title_source text
);

CREATE TABLE IF NOT EXISTS settings (
  key text PRIMARY KEY,
  value text NOT NULL
);

CREATE UNLOGGED TABLE IF NOT EXISTS tool_calls (
  id text PRIMARY KEY,
  session_id text,
  project text,
  ts text,
  tool text,
  server text,
  detail text
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS idx_tool_calls_server ON tool_calls(server);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts);
"""


# --- connection factories ----------------------------------------------------
def _pg_connect(autocommit: bool):
    import psycopg
    return psycopg.connect(_URL, autocommit=autocommit, row_factory=_hybrid_row)


def connect(db_path: str):
    """Init the schema (run once at startup) and return the connection."""
    if is_postgres():
        conn = _pg_connect(autocommit=True)
        conn.execute(_SCHEMA_PG)
        return _PgConn(conn)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # migrate pre-existing session_meta tables that lack title_source (sqlite)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(session_meta)")}
    if "title_source" not in cols:
        conn.execute("ALTER TABLE session_meta ADD COLUMN title_source TEXT")
    conn.commit()
    return conn


def open_write(db_path: str):
    """Long-lived WRITE connection (watcher/ingest, serialized by a lock).
    PostgreSQL: a dedicated connection (explicit commit). SQLite: WAL + bounded
    busy-timeout so readers never block on the writer."""
    if is_postgres():
        return _PgConn(_pg_connect(autocommit=False))
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def open_read(db_path: str):
    """Short-lived READ connection, one per request/thread. Caller closes it
    (or use `read_conn`). PostgreSQL: checked out of the pool (close = return)."""
    if is_postgres():
        return _PgConn(_POOL.getconn(), _POOL)
    c = sqlite3.connect(db_path, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    return c


@contextlib.contextmanager
def read_conn(db_path: str):
    """Context-managed read connection (preferred over open_read): closes/returns
    on exit, so call sites stop hand-writing try/finally."""
    c = open_read(db_path)
    try:
        yield c
    finally:
        c.close()
