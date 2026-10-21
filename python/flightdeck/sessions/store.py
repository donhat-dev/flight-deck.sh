"""SQL for session search. Nothing here decides policy — see service.py.

Two indexes answer two different questions and neither replaces the other:

  * `idx_message_text_tsv` (GIN over tsvector) answers "which session talked
    about X" — word-level, ranked, cheap.
  * `idx_message_text_trgm` (GIN over trigram) answers "where does this exact
    fragment appear" — substring-level, unranked, and the only one that can find
    `_sale` inside `nakivo_sale`.

Postgres-only. The tsvector column, the GIN indexes and `websearch_to_tsquery`
have no SQLite counterpart here, so a search on SQLite refuses with a stated
reason rather than degrading into something that silently finds less.
"""
from flightdeck import db

# Every result row is the same shape whether it came from a search or a read, so
# no caller has to branch on its origin.
_ROW = """m.uuid, m.session_id, m.project, m.role, m.ts, m.seq,
          m.text, m.tool_input, coalesce(sm.title, '') AS title"""

_FROM = """FROM message_text m
           LEFT JOIN session_meta sm ON sm.session_id = m.session_id"""

# How many matching rows the dedupe pass considers before the outer LIMIT. Large
# enough that dedupe sees the real duplicate groups, small enough that a
# one-common-word query does not sort the whole corpus.
_DEDUPE_SCAN = 2000


class SearchUnavailable(RuntimeError):
    """The engine cannot serve a search at all."""


def require_pg() -> None:
    if not db.is_postgres():
        raise SearchUnavailable(
            "session search requires PostgreSQL (set TOKEN_AUDIT_DATABASE_URL). "
            "The SQLite ledger stores message_text but carries no tsvector index.")


def parsed_query(conn, query: str) -> tuple[str, int]:
    """Return (rendered tsquery, node count).

    A node count of 0 means websearch_to_tsquery threw the whole query away —
    every term was punctuation or a stop word. That is NOT the same as "no
    results", and the caller must never report it as one. This is the single
    failure mode this feature exists to avoid.
    """
    row = conn.execute(
        "SELECT websearch_to_tsquery('vi_en', ?)::text AS q, "
        "       numnode(websearch_to_tsquery('vi_en', ?)) AS n",
        (query, query)).fetchone()
    return (row["q"] or ""), int(row["n"] or 0)


def count_unfiltered(conn, query: str, cap: int = 1000) -> int:
    """How many rows the query matches with NO filters applied, bounded by `cap`.

    Asked only when a filtered search came back empty, to answer the question the
    result set cannot: was the word absent from the corpus, or did the filters
    throw its matches away? Those are different answers and used to look
    identical. Bounded because the caller needs "any" and "how many, roughly" —
    never an exact count over a common word.
    """
    return int(conn.execute(
        "SELECT count(*) AS n FROM (SELECT 1 FROM message_text m "
        "CROSS JOIN websearch_to_tsquery('vi_en', ?) q "
        "WHERE m.tsv @@ q LIMIT ?) x", (query, cap)).fetchone()["n"])


def search_fts(conn, query: str, *, project=None, since=None, role=None,
               has_text=False, limit=10) -> list:
    """Ranked word search, one row per distinct content.

    The inner DISTINCT ON collapses resume/fork copies (52% of the corpus) and
    keeps the EARLIEST — the occurrence where the thing was actually said, not a
    later transcript that inherited it. `copies` reports what was collapsed, so a
    caller can tell a repeated message from a unique one. The inner LIMIT bounds
    the dedupe pass on a broad query; it is deliberately far above `limit`.
    """
    sql = f"""
        WITH q AS (SELECT websearch_to_tsquery('vi_en', ?) AS tsq),
        hits AS (
            SELECT DISTINCT ON (m.content_hash)
                   {_ROW}, m.content_hash,
                   ts_headline('vi_en', coalesce(m.text, m.tool_input), q.tsq,
                               'MaxWords=40, MinWords=18, MaxFragments=2') AS snippet,
                   ts_rank(m.tsv, q.tsq) AS rank,
                   count(*) OVER (PARTITION BY m.content_hash) AS copies
            {_FROM}
            CROSS JOIN q
            WHERE m.tsv @@ q.tsq
              AND (?::text IS NULL OR m.project = ?::text)
              AND (?::timestamptz IS NULL OR m.ts >= ?::timestamptz)
              AND (?::text IS NULL OR m.role = ?::text)
                  AND (NOT ?::boolean OR m.text IS NOT NULL)
            ORDER BY m.content_hash, m.ts
            LIMIT {_DEDUPE_SCAN}
        )
        SELECT * FROM hits ORDER BY rank DESC, ts DESC LIMIT ?
    """
    return conn.execute(sql, (query, project, project, since, since,
                              role, role, bool(has_text), limit)).fetchall()


def search_trigram(conn, fragment: str, *, project=None, since=None, role=None,
                   has_text=False, limit=10) -> list:
    """Substring search. A substring match carries no relevance score, so rows
    come back newest-first — recency is the only ordering that means anything."""
    sql = f"""
        WITH hits AS (
            SELECT DISTINCT ON (m.content_hash)
                   {_ROW}, m.content_hash,
                   NULL::text AS snippet, NULL::real AS rank,
                   count(*) OVER (PARTITION BY m.content_hash) AS copies
            {_FROM}
            WHERE (m.text ILIKE ? OR m.tool_input ILIKE ?)
              AND (?::text IS NULL OR m.project = ?::text)
              AND (?::timestamptz IS NULL OR m.ts >= ?::timestamptz)
              AND (?::text IS NULL OR m.role = ?::text)
                  AND (NOT ?::boolean OR m.text IS NOT NULL)
            ORDER BY m.content_hash, m.ts
            LIMIT {_DEDUPE_SCAN}
        )
        SELECT * FROM hits ORDER BY ts DESC LIMIT ?
    """
    pat = f"%{fragment}%"
    return conn.execute(sql, (pat, pat, project, project, since, since,
                              role, role, bool(has_text), limit)).fetchall()


def anchor(conn, uuid: str):
    """The (session_id, seq) a read window centres on."""
    return conn.execute(
        "SELECT session_id, seq FROM message_text WHERE uuid=?",
        (uuid,)).fetchone()


def window(conn, session_id: str, seq: int, before: int, after: int) -> list:
    """The `before` messages preceding `seq` and the `after` following it.

    Neighbours are selected by RANK, not by a range on `seq`. `seq` is the
    absolute line number in the JSONL file and ingest keeps only the ~3% of lines
    that carry conversation, so consecutive messages sit 5, 20 or 50 apart;
    `BETWEEN seq-2 AND seq+2` lands inside a gap and returns the anchor alone.
    """
    rows = conn.execute(
        f"""(SELECT {_ROW} {_FROM}
             WHERE m.session_id=? AND m.seq < ? ORDER BY m.seq DESC LIMIT ?)
            UNION ALL
            (SELECT {_ROW} {_FROM}
             WHERE m.session_id=? AND m.seq >= ? ORDER BY m.seq LIMIT ?)""",
        (session_id, seq, before, session_id, seq, after + 1)).fetchall()
    return sorted(rows, key=lambda r: r["seq"])


def bookend(conn, session_id: str, n: int) -> tuple[list, list]:
    """First and last `n` messages — a session's goal and its conclusion, without
    paying for the middle."""
    first = conn.execute(
        f"SELECT {_ROW} {_FROM} WHERE m.session_id=? ORDER BY m.seq LIMIT ?",
        (session_id, n)).fetchall()
    last = conn.execute(
        f"SELECT {_ROW} {_FROM} WHERE m.session_id=? ORDER BY m.seq DESC LIMIT ?",
        (session_id, n)).fetchall()
    return first, list(reversed(last))


def session_exists(conn, session_id: str) -> bool:
    return bool(conn.execute(
        "SELECT EXISTS(SELECT 1 FROM message_text WHERE session_id=?)",
        (session_id,)).fetchone()[0])


def corpus_stats(conn) -> dict:
    row = conn.execute(
        "SELECT count(*) AS messages, count(DISTINCT session_id) AS sessions "
        "FROM message_text").fetchone()
    return {"messages": row["messages"], "sessions": row["sessions"]}
