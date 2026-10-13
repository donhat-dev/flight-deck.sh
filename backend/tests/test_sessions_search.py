"""Session search: extraction rules, query routing, and the live-corpus checks.

The DB-backed tests skip unless TOKEN_AUDIT_DATABASE_URL points at a PostgreSQL
with an ingested corpus — they are the only place the tsvector/trigram behaviour
can actually be observed, and asserting it against a mock would assert the mock.
"""
import json
import os

import pytest

from flightdeck import ingest
from flightdeck.sessions import service, store


# --------------------------------------------------------------------------
# Extraction — pure, no DB.
# --------------------------------------------------------------------------

def _line(**kw):
    base = {"type": "assistant", "uuid": "u1", "sessionId": "s1",
            "cwd": "/p", "timestamp": "2026-08-18T00:00:00Z", "message": {}}
    base.update(kw)
    return base


def test_text_block_is_kept():
    row = ingest.text_row(_line(message={"content": [
        {"type": "text", "text": "hello world"}]}), 7)
    assert row["text"] == "hello world"
    assert row["seq"] == 7
    assert row["role"] == "assistant"


def test_tool_result_and_image_are_dropped():
    """65% of the corpus by bytes. Indexing them returns noise, not answers."""
    row = ingest.text_row(_line(message={"content": [
        {"type": "tool_result", "content": "x" * 5000},
        {"type": "image", "source": {"data": "y" * 5000}}]}), 1)
    assert row is None


def test_tool_use_input_is_kept_and_capped():
    payload = {"command": "z" * 4000}
    row = ingest.text_row(_line(message={"content": [
        {"type": "tool_use", "input": payload}]}), 1)
    assert row["text"] is None
    assert len(row["tool_input"]) <= ingest._MAX_TOOL_INPUT_CHARS
    assert row["tool_input"].startswith('{"command"')


def test_non_chat_types_are_skipped():
    for t in ("attachment", "file-history-snapshot", "queue-operation",
              "mode", "last-prompt", "system"):
        assert ingest.text_row(_line(type=t), 1) is None


def test_string_content_is_accepted():
    row = ingest.text_row(_line(type="user", message={"content": "plain"}), 3)
    assert row["text"] == "plain" and row["role"] == "user"


def test_empty_message_yields_no_row():
    assert ingest.text_row(_line(message={"content": []}), 1) is None
    assert ingest.text_row(_line(message={"content": [
        {"type": "text", "text": "   "}]}), 1) is None


def test_text_is_capped():
    row = ingest.text_row(_line(message={"content": [
        {"type": "text", "text": "a" * 50000}]}), 1)
    assert len(row["text"]) == ingest._MAX_TEXT_CHARS


def test_unicode_survives_tool_input_serialisation():
    row = ingest.text_row(_line(message={"content": [
        {"type": "tool_use", "input": {"q": "tìm kiếm"}}]}), 1)
    assert "tìm kiếm" in row["tool_input"]
    assert json.loads(row["tool_input"])["q"] == "tìm kiếm"


# --------------------------------------------------------------------------
# Routing policy — pure.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("q,expected", [
    (".env", True), ("_sale", True), ("CRM-121", True),
    ("two words", False), ("ab", False), ("", False),
])
def test_fragment_detection(q, expected):
    assert service._is_fragment(q) is expected


def test_clip_reports_what_it_dropped():
    out = service._clip("x" * 100, 10)
    assert out.startswith("x" * 10) and "+90 chars" in out
    assert service._clip("short", 10) == "short"
    assert service._clip(None, 10) is None


# --------------------------------------------------------------------------
# Live corpus.
# --------------------------------------------------------------------------

# Opt-in under its OWN variable, never the ambient TOKEN_AUDIT_DATABASE_URL that
# conftest strips. That rule exists because the suite once wrote rows into
# production; these checks are read-only, but "explicit, never ambient" is the
# property that keeps it that way. Run them with:
#
#   FLIGHTDECK_LIVE_CORPUS_DSN="$TOKEN_AUDIT_DATABASE_URL" pytest tests/test_sessions_search.py
_LIVE_DSN = os.environ.get("FLIGHTDECK_LIVE_CORPUS_DSN")

pg = pytest.mark.skipif(
    not _LIVE_DSN, reason="set FLIGHTDECK_LIVE_CORPUS_DSN to run live-corpus checks")


@pytest.fixture
def conn(monkeypatch):
    """A read-only connection to the live corpus, configured explicitly."""
    from flightdeck import db
    monkeypatch.setattr(db, "_URL", _LIVE_DSN, raising=False)
    db.configure({"database_url": _LIVE_DSN})
    c = db.open_write(":memory:")
    if store.corpus_stats(c)["messages"] == 0:
        pytest.skip("corpus is empty; run an ingest sweep first")
    yield c
    c.close()


@pg
def test_broken_query_is_an_error_not_an_empty_result(conn):
    """The failure this whole feature exists to avoid. A query that parses to
    nothing must never look like a corpus that had nothing."""
    r = service.search(conn, '"')
    assert "error" in r and r["error"] == "query parsed to nothing"
    assert r["results"] == []


@pg
def test_true_empty_is_reported_as_empty(conn):
    r = service.search(conn, "zzzznotpresentxyzqq")
    assert "error" not in r and r["count"] == 0


@pg
def test_dot_env_is_searchable(conn):
    """Hermes PR #43889 class: a leading dot silently returned zero there."""
    r = service.search(conn, ".env", limit=3)
    assert "error" not in r and r["count"] > 0


@pg
def test_accents_fold_both_ways(conn):
    assert service.search(conn, "tim kiem", limit=3)["count"] > 0
    assert service.search(conn, "tìm kiếm", limit=3)["count"] > 0


@pg
def test_identifier_becomes_a_phrase_query(conn):
    r = service.search(conn, "nakivo_sale", limit=3)
    assert r["count"] > 0
    assert "<->" in r["tsquery"], "underscore identifier must stay adjacent"


@pg
def test_results_are_deduped_by_content(conn):
    """Resumed/forked transcripts copy history under new uuids — 52% of rows."""
    r = service.search(conn, "subscription", limit=10)
    hashes = [h["snippet"] for h in r["results"]]
    assert len(hashes) == len(set(hashes))
    assert all(h["copies"] >= 1 for h in r["results"])


@pg
def test_read_window_spans_the_seq_gaps(conn):
    """`seq` is a jsonl line number and ~97% of lines are skipped, so a window
    must be selected by rank, not by arithmetic on seq."""
    hit = service.search(conn, "subscription", limit=1)["results"][0]
    w = service.read(conn, uuid=hit["uuid"], before=3, after=3)
    assert w["count"] > 1
    assert [m["seq"] for m in w["messages"]] == sorted(m["seq"] for m in w["messages"])


@pg
def test_window_is_clamped(conn):
    hit = service.search(conn, "subscription", limit=1)["results"][0]
    w = service.read(conn, uuid=hit["uuid"], before=9999, after=9999)
    assert w["count"] <= service.MAX_WINDOW * 2 + 1


@pg
def test_bookend_returns_both_ends(conn):
    hit = service.search(conn, "subscription", limit=1)["results"][0]
    sid = service.read(conn, uuid=hit["uuid"])["session_id"]
    b = service.read(conn, session_id=sid, mode="bookend", before=2)
    assert 1 <= len(b["first"]) <= 2 and 1 <= len(b["last"]) <= 2


@pg
def test_unknown_anchors_say_so(conn):
    assert "error" in service.read(conn, uuid="not-a-uuid")
    assert "error" in service.read(conn, mode="bookend")
    assert "error" in service.search(conn, "")


@pg
def test_limit_is_clamped(conn):
    r = service.search(conn, "subscription", limit=9999)
    assert r["count"] <= service.MAX_LIMIT


# --------------------------------------------------------------------------
# Red-team regressions. Each one is a defect the adversarial pass actually
# found against the live corpus, not a hypothetical.
# --------------------------------------------------------------------------

@pg
def test_filters_excluding_everything_is_not_reported_as_empty(conn):
    """F1: the headline invariant only covered `query`. Pushing the cause into a
    filter brought the silent empty straight back."""
    r = service.search(conn, "subscription", project="/no/such/project")
    assert r["count"] == 0
    assert r["reason"] == "filters excluded every match"
    # Bounded count: an exact number under the cap, or "1000+" at it.
    mb = r["matched_before_filters"]
    assert (int(mb.rstrip("+")) if isinstance(mb, str) else mb) > 0
    assert r["filters"] == {"project": "/no/such/project"}


@pg
def test_a_filtered_empty_does_not_trigger_the_substring_scan(conn):
    """F2: the fallback fired on 'FTS returned nothing' without asking why, so a
    bad filter bought a full ILIKE scan that could only return nothing too."""
    r = service.search(conn, "subscription", project="/no/such/project")
    assert r["mode"] == "fts"


@pg
def test_a_genuinely_absent_word_still_falls_back(conn):
    r = service.search(conn, "zzzznotpresentxyzqq")
    assert r["mode"] == "trigram-fallback" and r["count"] == 0
    assert "reason" not in r


@pg
def test_bad_since_is_a_stated_error_not_a_psycopg_traceback(conn):
    """F3: the raw InvalidDatetimeFormat leaked, including parameter positions."""
    r = service.search(conn, "subscription", since="not-a-date")
    assert r["error"] == "invalid `since`"
    assert "psycopg" not in str(r) and "parameter $" not in str(r)


@pg
def test_since_accepts_both_iso_shapes(conn):
    for value in ("2026-08-01", "2026-08-01T09:30:00+00:00", "2026-08-01T09:30:00Z"):
        assert "error" not in service.search(conn, "subscription", since=value)


@pg
def test_two_character_cjk_substring_is_findable(conn):
    """F4: `作用` returned empty while `副作用` sat in the corpus, because the
    fragment floor was 3 characters and CJK has no spaces to reach it."""
    assert service.search(conn, "副作用", limit=1)["count"] > 0
    r = service.search(conn, "作用", limit=1)
    assert r["count"] > 0 and r["mode"].startswith("trigram")


@pg
def test_short_latin_query_says_it_is_short(conn):
    r = service.search(conn, "zq")   # verified absent from the corpus
    assert r["count"] == 0 and r.get("reason") == "too short for a substring search"


@pg
def test_snippets_shrink_as_the_result_count_grows(conn):
    """F5: one limit=50 call returned ~15k tokens from a tool sold as cheap."""
    wide = service.search(conn, "subscription", limit=20)
    narrow = service.search(conn, "subscription", limit=2)
    def longest(r):
        return max((len(h["snippet"] or "") for h in r["results"]), default=0)
    assert longest(wide) < longest(narrow)
    assert sum(len(h["snippet"] or "") for h in wide["results"]) <= 20 * 400


@pg
def test_negation_only_queries_do_not_claim_a_ranking(conn):
    """F6: every hit scored 0.0, so the order was recency wearing a rank."""
    r = service.search(conn, "nakivo -subscription -helpdesk", limit=3)
    if r["count"]:
        assert r["ranked"] is False
        assert all(h["rank"] is None for h in r["results"])


@pg
def test_has_text_keeps_only_rows_carrying_text(conn):
    """F7: the flag was named prose_only but never judged prose."""
    r = service.search(conn, "subscription", has_text=True, limit=5)
    assert all(h["snippet"] for h in r["results"])


@pg
@pytest.mark.parametrize("payload", [
    "' OR 1=1 --", "'; DROP TABLE message_text; --", "x' OR '1'='1",
])
def test_injection_payloads_are_data(conn, payload):
    service.search(conn, payload, limit=1)
    service.search(conn, "subscription", project=payload, limit=1)
    service.read(conn, uuid=payload)
    service.read(conn, session_id=payload, mode="bookend")
    assert store.corpus_stats(conn)["messages"] > 0
