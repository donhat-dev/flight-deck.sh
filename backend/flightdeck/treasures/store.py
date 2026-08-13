"""The `treasures` index: one row per artifact, pointing at files on disk.

The filestore is the system of record for content (each artifact dir carries a
meta.json sidecar); this table is a rebuildable index that makes the library
queryable and — because `origin_id` holds the Claude session id — joinable
against FlightDeck's existing messages / tool_calls / session_meta rows.

Portable SQL only: `?` placeholders, ON CONFLICT upserts, TEXT ISO timestamps.
The same DDL runs on SQLite (tests) and PostgreSQL (production), where the table
is LOGGED because artifacts are user content, not derived ingest data.
"""
from datetime import datetime, timezone

from flightdeck import db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


COLUMNS = (
    "id", "title", "slug", "dir_path", "kind", "language", "status", "version",
    "source_format", "source_checksum", "render_checksum", "render_bytes",
    "origin_kind", "origin_id", "origin_path", "published_url", "duplicate_of",
    "authored_at", "ingested_at", "updated_at", "font", "custom_head",
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
  updated_at      text NOT NULL,
  font            text,
  custom_head     text
)
"""

# Tags live in their own table rather than a comma-joined column, so filtering
# is an indexed join instead of a LIKE over a string, and a rename is one UPDATE.
# ON DELETE CASCADE means service.delete needs no extra step: dropping the
# artifact row drops its tags with it.
_DDL_TAGS = """
CREATE TABLE IF NOT EXISTS treasure_tags (
  treasure_id text NOT NULL REFERENCES treasures(id) ON DELETE CASCADE,
  tag         text NOT NULL,
  PRIMARY KEY (treasure_id, tag)
)
"""

# Site-wide Treasures defaults (agent notes, header/footer HTML), NOT
# per-artifact `custom_head`: those splice into one artifact's <head>, these
# splice into the visible <body> of every artifact rendered afterwards (see
# render.inject_body_defaults). Key-value on purpose — a fourth default later
# is one INSERT, not a schema migration and a column-add on every dialect.
CONFIG_KEYS = ("default_agent_notes", "default_header_html", "default_footer_html")

_DDL_CONFIG = """
CREATE TABLE IF NOT EXISTS treasure_config (
  key        text PRIMARY KEY,
  value      text NOT NULL,
  updated_at text NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_treasures_origin ON treasures(origin_id)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_status ON treasures(status)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_slug ON treasures(slug)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_srcsum ON treasures(source_checksum)",
    # origin_root filters are prefix scans on this column.
    "CREATE INDEX IF NOT EXISTS idx_treasures_originpath ON treasures(origin_path)",
    "CREATE INDEX IF NOT EXISTS idx_treasure_tags_tag ON treasure_tags(tag)",
)


def init(conn) -> None:
    """Create the tables + indexes if absent. Safe to call on every startup."""
    conn.execute(_DDL)
    conn.execute(_DDL_TAGS)
    conn.execute(_DDL_CONFIG)
    for stmt in _INDEXES:
        conn.execute(stmt)
    _ensure_font_column(conn)
    _ensure_custom_head_column(conn)
    conn.commit()


# --- tags -------------------------------------------------------------------
def _clean_tags(tags) -> list[str]:
    """Normalise to lowercase, trimmed, deduped, order preserved.

    Normalising on the way IN is what makes `tag=` filtering exact-match: with
    raw input, `Billing`, `billing ` and `billing` would be three tags.
    """
    out, seen = [], set()
    for t in tags or []:
        v = str(t).strip().lower()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def tags_of(conn, treasure_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM treasure_tags WHERE treasure_id=? ORDER BY tag",
        (treasure_id,)).fetchall()
    return [r[0] for r in rows]


def set_tags(conn, treasure_id: str, tags) -> list[str]:
    """Replace the artifact's tag set."""
    conn.execute("DELETE FROM treasure_tags WHERE treasure_id=?", (treasure_id,))
    for tag in _clean_tags(tags):
        conn.execute(
            "INSERT INTO treasure_tags (treasure_id, tag) VALUES (?, ?)",
            (treasure_id, tag))
    conn.commit()
    return tags_of(conn, treasure_id)


def add_tags(conn, treasure_id: str, tags) -> list[str]:
    existing = set(tags_of(conn, treasure_id))
    for tag in _clean_tags(tags):
        if tag not in existing:
            conn.execute(
                "INSERT INTO treasure_tags (treasure_id, tag) VALUES (?, ?)",
                (treasure_id, tag))
    conn.commit()
    return tags_of(conn, treasure_id)


def remove_tags(conn, treasure_id: str, tags) -> list[str]:
    for tag in _clean_tags(tags):
        conn.execute(
            "DELETE FROM treasure_tags WHERE treasure_id=? AND tag=?",
            (treasure_id, tag))
    conn.commit()
    return tags_of(conn, treasure_id)


def all_tags(conn) -> list[dict]:
    """Every tag in use with its artifact count, most-used first."""
    rows = conn.execute(
        "SELECT tag, COUNT(*) AS n FROM treasure_tags "
        "GROUP BY tag ORDER BY n DESC, tag ASC").fetchall()
    return [{"tag": r[0], "count": r[1]} for r in rows]


def tags_for(conn, treasure_ids) -> dict[str, list[str]]:
    """Tags for many artifacts in ONE query, keyed by id.

    Listing 97 rows must not become 97 tag queries.
    """
    ids = list(treasure_ids)
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT treasure_id, tag FROM treasure_tags "
        f"WHERE treasure_id IN ({marks}) ORDER BY tag", tuple(ids)).fetchall()
    out: dict[str, list[str]] = {i: [] for i in ids}
    for r in rows:
        out.setdefault(r[0], []).append(r[1])
    return out


def delete_tags(conn, treasure_id: str) -> None:
    """Drop an artifact's tags.

    The FK carries ON DELETE CASCADE, but SQLite does not enforce foreign keys
    unless `PRAGMA foreign_keys=ON` is set per connection — so the delete path
    does it explicitly and the constraint stays as the Postgres-side guarantee
    against orphans.
    """
    conn.execute("DELETE FROM treasure_tags WHERE treasure_id=?", (treasure_id,))
    conn.commit()


def _ensure_font_column(conn) -> None:
    """Migration: add font (default/space-grotesk/jetbrains-mono) to tables
    created before font selection existed. NULL means "space-grotesk"."""
    if db.is_postgres():
        conn.execute("ALTER TABLE treasures ADD COLUMN IF NOT EXISTS font text")
    else:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(treasures)").fetchall()}
        if "font" not in cols:
            conn.execute("ALTER TABLE treasures ADD COLUMN font TEXT")


def _ensure_custom_head_column(conn) -> None:
    """Migration: add custom_head (raw HTML injected before </head>)."""
    if db.is_postgres():
        conn.execute("ALTER TABLE treasures ADD COLUMN IF NOT EXISTS custom_head text")
    else:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(treasures)").fetchall()}
        if "custom_head" not in cols:
            conn.execute("ALTER TABLE treasures ADD COLUMN custom_head TEXT")


def config_get(conn) -> dict:
    """Every default key, always present — "" when never set, plus a top-level
    `updated_at` (the latest of the keys actually written, None on a virgin
    database). No caller has to special-case a database that has never seen a
    PUT/treasure_config_set."""
    rows = conn.execute(
        "SELECT key, value, updated_at FROM treasure_config").fetchall()
    by_key = {r[0]: (r[1], r[2]) for r in rows}
    out, updated_at = {}, None
    for key in CONFIG_KEYS:
        val, ts = by_key.get(key, ("", None))
        out[key] = val
        if ts and (updated_at is None or ts > updated_at):
            updated_at = ts
    out["updated_at"] = updated_at
    return out


def config_set(conn, values: dict) -> dict:
    """Upsert only the given keys, leaving the rest untouched. Raises
    ValueError on an unknown key — nothing is written in that case, not even
    the other, valid keys in the same call."""
    unknown = sorted(set(values) - set(CONFIG_KEYS))
    if unknown:
        raise ValueError(
            f"unknown config key(s): {', '.join(unknown)} "
            f"(expected one of {', '.join(CONFIG_KEYS)})")
    ts = _now_iso()
    for key, val in values.items():
        conn.execute(
            "INSERT INTO treasure_config (key, value, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (key, val, ts))
    conn.commit()
    return config_get(conn)


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


def delete(conn, art_id: str) -> int:
    """Remove one index row by primary key. Returns the rows affected (0 or 1).
    The caller owns removing the files — see service.delete, which guards the
    path before touching the filesystem."""
    cur = conn.execute("DELETE FROM treasures WHERE id=?", (art_id,))
    conn.commit()
    return cur.rowcount


def list_rows(conn, *, status=None, language=None, kind=None, origin_id=None,
              query=None, origin_root=None, tag=None,
              limit=100, offset=0) -> list[dict]:
    """Filtered, newest-first listing.

    `query`       matches title or slug.
    `origin_root` matches the START of origin_path, so one call answers "every
                  artifact that came out of this folder (or this site)". Works
                  for a filesystem root and for a URL prefix alike, since both
                  are just origin_path prefixes.
    `tag`         restricts to artifacts carrying that tag.
    """
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
    if origin_root:
        # Prefix match, not LIKE %…%: a root is an anchor, and escaping the
        # wildcards a path can contain is not worth it for a substring search.
        # ESCAPE is spelled out because SQLite has NO default escape character
        # for LIKE — without it the backslashes below would be literal and the
        # escaping would silently do nothing.
        where.append("origin_path LIKE ? ESCAPE '\\'")
        params.append(_like_prefix(origin_root))
    if tag:
        where.append("id IN (SELECT treasure_id FROM treasure_tags WHERE tag=?)")
        params.append(tag.strip().lower())
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ",".join(COLUMNS)
    params += [int(limit), int(offset)]
    rows = conn.execute(
        f"SELECT {cols} FROM treasures{clause} "
        f"ORDER BY updated_at DESC LIMIT ? OFFSET ?", tuple(params)).fetchall()
    return [_as_dict(r) for r in rows]


def _like_prefix(root: str) -> str:
    """`root` as a LIKE prefix pattern, with LIKE's own wildcards escaped.

    A path or URL may legitimately contain `%` or `_` (`report_v2`, an
    encoded space), and an unescaped `_` matches any character — so a naive
    pattern would quietly over-match.
    """
    escaped = root.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
