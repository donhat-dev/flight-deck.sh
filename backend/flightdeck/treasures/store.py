"""The `treasures` index: one row per artifact, pointing at files on disk.

The filestore is the system of record for content (each artifact dir carries a
meta.json sidecar); this table is a rebuildable index that makes the library
queryable and — because `origin_id` holds the Claude session id — joinable
against FlightDeck's existing messages / tool_calls / session_meta rows.

Portable SQL only: `?` placeholders, ON CONFLICT upserts, TEXT ISO timestamps.
The same DDL runs on SQLite (tests) and PostgreSQL (production), where the table
is LOGGED because artifacts are user content, not derived ingest data.
"""

COLUMNS = (
    "id", "title", "slug", "dir_path", "kind", "language", "status", "version",
    "source_format", "source_checksum", "render_checksum", "render_bytes",
    "origin_kind", "origin_id", "origin_path", "published_url", "duplicate_of",
    "authored_at", "ingested_at", "updated_at",
)

_DDL = """
CREATE TABLE IF NOT EXISTS treasures (
  id              text PRIMARY KEY,
  title           text NOT NULL,
  slug            text NOT NULL,
  dir_path        text NOT NULL,
  kind            text NOT NULL,
  language        text NOT NULL,
  status          text NOT NULL,
  version         integer NOT NULL,
  source_format   text NOT NULL,
  source_checksum text,
  render_checksum text,
  render_bytes    bigint,
  origin_kind     text,
  origin_id       text,
  origin_path     text,
  published_url   text,
  duplicate_of    text,
  authored_at     text,
  ingested_at     text NOT NULL,
  updated_at      text NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_treasures_origin ON treasures(origin_id)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_status ON treasures(status)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_slug ON treasures(slug)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_srcsum ON treasures(source_checksum)",
)


def init(conn) -> None:
    """Create the table + indexes if absent. Safe to call on every startup."""
    conn.execute(_DDL)
    for stmt in _INDEXES:
        conn.execute(stmt)
    conn.commit()


def upsert(conn, row: dict) -> dict:
    """Insert or update by primary key; returns the stored row."""
    cols = ",".join(COLUMNS)
    placeholders = ",".join("?" for _ in COLUMNS)
    updates = ",".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id")
    conn.execute(
        f"INSERT INTO treasures ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        tuple(row.get(c) for c in COLUMNS))
    conn.commit()
    return get(conn, row["id"])


def _as_dict(r) -> dict:
    return {c: r[c] for c in COLUMNS}


def get(conn, ident: str) -> dict | None:
    """Fetch by id, falling back to slug."""
    cols = ",".join(COLUMNS)
    r = conn.execute(
        f"SELECT {cols} FROM treasures WHERE id=?", (ident,)).fetchone()
    if r is None:
        r = conn.execute(
            f"SELECT {cols} FROM treasures WHERE slug=? "
            f"ORDER BY updated_at DESC", (ident,)).fetchone()
    return _as_dict(r) if r is not None else None


def list_rows(conn, *, status=None, language=None, kind=None, origin_id=None,
              query=None, limit=100, offset=0) -> list[dict]:
    """Filtered, newest-first listing. `query` matches title or slug."""
    where, params = [], []
    for col, val in (("status", status), ("language", language),
                     ("kind", kind), ("origin_id", origin_id)):
        if val:
            where.append(f"{col}=?")
            params.append(val)
    if query:
        where.append("(lower(title) LIKE ? OR lower(slug) LIKE ?)")
        like = f"%{query.lower()}%"
        params += [like, like]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ",".join(COLUMNS)
    params += [int(limit), int(offset)]
    rows = conn.execute(
        f"SELECT {cols} FROM treasures{clause} "
        f"ORDER BY updated_at DESC LIMIT ? OFFSET ?", tuple(params)).fetchall()
    return [_as_dict(r) for r in rows]
