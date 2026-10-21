"""Search policy: which index answers a query, and how much of a hit comes back.

The one rule this module exists to enforce: **an empty result and a broken query
are never the same answer.** Postgres' `websearch_to_tsquery` does not raise on
junk input the way SQLite's FTS5 `MATCH` does — it quietly returns an empty
tsquery, which matches nothing, which looks exactly like "nothing was found".
`parsed_query` gives us the node count so we can tell the two apart and say so.
"""
from datetime import datetime

from flightdeck.sessions import store

# A hit is a pointer, not a payload: enough to decide whether to spend context on
# session_read, and no more.
_SNIPPET_CHARS = 400
# Total snippet budget for one search. A red-team call with limit=50 returned
# ~15k tokens, which is not the "cheap pointer" the tool advertises. Snippets
# shrink as the result count grows so a wide search costs about what a narrow
# one does.
_SNIPPET_BUDGET = 4000
# A read is the payload. 4000 matches what a message is worth carrying before it
# starts crowding out the reason it was fetched.
_READ_CHARS = 4000
_BOOKEND_CHARS = 1200

MAX_LIMIT = 20
MAX_WINDOW = 40

# ts_rank returns 1e-20 — not 0 — for a row that matched without contributing any
# rankable weight, which is what a negation-only query (`-foo -bar`) produces.
# Comparing against 0.0 never fired; the scores then rounded to a convincing
# "0.0" in the output and passed for a ranking.
_RANK_EPSILON = 1e-12

# CJK writing has no spaces, so a meaningful substring is routinely 2 characters
# (`作用` inside `副作用`). Holding every language to a 3-character floor made
# those queries return an unexplained empty.
_CJK = (
    (0x3040, 0x30FF),   # kana
    (0x3400, 0x4DBF),   # CJK ext A
    (0x4E00, 0x9FFF),   # CJK unified
    (0xAC00, 0xD7AF),   # hangul
    (0xF900, 0xFAFF),   # compatibility
)


def _has_cjk(s: str) -> bool:
    return any(any(lo <= ord(c) <= hi for lo, hi in _CJK) for c in s)


def _min_fragment_len(q: str) -> int:
    return 2 if _has_cjk(q) else 3


def parse_since(raw):
    """Accept an ISO date or timestamp, or say why it was rejected.

    Handing the raw string to Postgres let a psycopg `InvalidDatetimeFormat`
    (with its internal parameter positions) escape as the tool's error message.
    A bad argument is the caller's mistake and deserves the caller's vocabulary.
    """
    if raw in (None, ""):
        return None, None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text), None
    except ValueError:
        return None, {
            "error": "invalid `since`",
            "detail": f"{raw!r} is not an ISO date or timestamp",
            "hint": "use '2026-08-01' or '2026-08-01T09:30:00+00:00'",
        }


def _clip(s, limit):
    if not s:
        return None
    s = str(s)
    return s if len(s) <= limit else s[:limit] + f" … [+{len(s) - limit:,} chars]"


def _hit(row, limit_chars, ranked=True):
    """Shape one row. `snippet` comes from ts_headline on the FTS path; on the
    trigram path there is no tsquery to headline with, so the text is clipped.

    `ranked=False` nulls the score instead of reporting a uniform 0.0. A query
    made only of exclusions (`-foo -bar`) scores every match at zero, so the
    order is really recency — printing 0.0 dressed that up as a ranking.
    """
    snippet = row["snippet"] or row["text"] or row["tool_input"]
    rank = row["rank"]
    return {
        "uuid": row["uuid"],
        "session_id": row["session_id"],
        "title": row["title"] or None,
        "project": row["project"],
        "role": row["role"],
        "ts": str(row["ts"]) if row["ts"] else None,
        "seq": row["seq"],
        "snippet": _clip(snippet, limit_chars),
        "rank": round(float(rank), 4) if (ranked and rank is not None) else None,
        # >1 means this exact content also appears in resumed/forked transcripts.
        # The row shown is the earliest one — where it was actually said.
        "copies": int(row["copies"]) if row.get("copies") else 1,
    }


def _message(row, limit_chars):
    out = {
        "uuid": row["uuid"],
        "role": row["role"],
        "ts": str(row["ts"]) if row["ts"] else None,
        "seq": row["seq"],
        "text": _clip(row["text"], limit_chars),
    }
    if row["tool_input"]:
        out["tool_input"] = _clip(row["tool_input"], 600)
    return out


def _is_fragment(q: str) -> bool:
    """A single unspaced token long enough for a trigram lookup — the shape word
    search handles worst (`.env`, `_sale`, `CRM-121`, `作用`)."""
    return len(q) >= _min_fragment_len(q) and not any(c.isspace() for c in q)


def search(conn, query, *, project=None, since=None, role=None,
           has_text=False, limit=10):
    store.require_pg()
    q = (query or "").strip()
    if not q:
        return {"error": "empty query", "hint": "pass a word, phrase or fragment"}
    since_dt, bad_since = parse_since(since)
    if bad_since:
        return bad_since
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    snippet_chars = max(120, min(_SNIPPET_CHARS, _SNIPPET_BUDGET // limit))
    filters = {k: v for k, v in
               (("project", project), ("role", role), ("since", since))
               if v not in (None, "")}
    hunt = dict(project=project, since=since_dt, role=role,
                has_text=has_text, limit=limit)

    tsq, nodes = store.parsed_query(conn, q)
    fragment = _is_fragment(q)
    ranked = True

    if nodes == 0:
        # The query parsed to nothing. A query problem, never an empty result —
        # unless it is fragment-shaped, which the substring index can serve.
        if not fragment:
            return {
                "error": "query parsed to nothing",
                "detail": f"every term in {q!r} was punctuation or a stop word",
                "hint": f"try a longer word, or a {_min_fragment_len(q)}+ "
                        "character fragment with no spaces to run a substring "
                        "search instead",
                "results": [],
            }
        rows, mode, ranked = store.search_trigram(conn, q, **hunt), "trigram", False
    else:
        rows = store.search_fts(conn, q, **hunt)
        mode = "fts"
        if not rows:
            # An empty result has two causes that used to look identical: the
            # word is absent, or the filters threw its matches away. Ask before
            # answering — and only widen to the substring scan for the first,
            # since a substring scan under the same filters finds nothing too.
            cap = 1000
            total = store.count_unfiltered(conn, q, cap)
            if total and filters:
                return {
                    "query": q, "mode": mode, "tsquery": tsq or None,
                    "count": 0, "results": [],
                    "reason": "filters excluded every match",
                    # The count is bounded, so say so rather than report the cap
                    # as if it were the real total.
                    "matched_before_filters": f"{cap}+" if total >= cap else total,
                    "filters": filters,
                    "hint": "drop or widen a filter; the corpus does contain "
                            "this query",
                }
            if fragment:
                rows, mode, ranked = (store.search_trigram(conn, q, **hunt),
                                      "trigram-fallback", False)
        # A negation-only query carries no rankable weight, so the ordering is
        # recency, not relevance. Say so instead of printing a fake 0.0.
        if rows and mode == "fts" and all(
                float(r["rank"] or 0) < _RANK_EPSILON for r in rows):
            ranked = False

    if not rows and not fragment and len(q) < _min_fragment_len(q):
        return {
            "query": q, "mode": mode, "tsquery": tsq or None,
            "count": 0, "results": [],
            "reason": "too short for a substring search",
            "hint": f"substring search needs {_min_fragment_len(q)}+ characters",
        }

    return {
        "query": q,
        "mode": mode,
        "ranked": ranked,
        "tsquery": tsq or None,
        "count": len(rows),
        "results": [_hit(r, snippet_chars, ranked=ranked) for r in rows],
    }


def read(conn, *, uuid=None, session_id=None, before=5, after=5, mode="window"):
    store.require_pg()
    before = max(0, min(int(before if before is not None else 5), MAX_WINDOW))
    after = max(0, min(int(after if after is not None else 5), MAX_WINDOW))

    if mode == "bookend":
        if not session_id:
            return {"error": "bookend mode needs session_id"}
        if not store.session_exists(conn, session_id):
            return {"error": f"no indexed messages for session {session_id}"}
        n = max(1, min(before or 3, 10))
        first, last = store.bookend(conn, session_id, n)
        return {
            "session_id": session_id,
            "mode": "bookend",
            "first": [_message(r, _BOOKEND_CHARS) for r in first],
            "last": [_message(r, _BOOKEND_CHARS) for r in last],
        }

    if not uuid:
        return {"error": "window mode needs uuid",
                "hint": "take it from a session_search result, or pass "
                        "mode='bookend' with session_id"}
    a = store.anchor(conn, uuid)
    if not a:
        return {"error": f"unknown uuid {uuid}",
                "hint": "uuids come from session_search results"}
    rows = store.window(conn, a["session_id"], a["seq"], before, after)
    return {
        "session_id": a["session_id"],
        "mode": "window",
        "anchor_uuid": uuid,
        "count": len(rows),
        "messages": [_message(r, _READ_CHARS) for r in rows],
    }
