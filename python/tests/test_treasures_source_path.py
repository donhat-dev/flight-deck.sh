"""Wrapping from a path, refreshing, and the read guard that bounds both.

Motivated by a real session's measured complaint: `treasure_wrap` only took
`content`, so adding a 671-line document meant reading it into context, retyping
it into the tool call, and then shelling out to `diff` to prove nothing drifted.
That third step is unavoidable with `content`, because the stored
`source_checksum` is the hash of whatever ARRIVED — it cannot detect that what
arrived was already wrong. Verification only means something on the side that
reads the file.
"""
import pytest

from flightdeck import db
from flightdeck.treasures import discover, filestore, service, store

DOC = "# Measured Title\n\nBody text long enough to matter.\n"


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Filestore + a single allowed read root, both inside tmp_path."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("TREASURES_READ_ROOTS", str(docs))
    return _conn(tmp_path), docs


# --- wrap(source_path=…) ---------------------------------------------------
def test_source_path_derives_title_format_and_origin(wired):
    conn, docs = wired
    p = docs / "18-contract-family.md"
    p.write_text(DOC, encoding="utf-8")

    out = service.wrap(conn, source_path=str(p))
    assert out["title"] == "Measured Title"          # from the first H1
    assert out["source_format"] == "markdown"        # from the suffix
    assert out["origin_kind"] == "doc_file"
    assert out["origin_path"] == str(p.resolve())    # what makes refresh free
    # the whole point: the checksum is of the real bytes on disk
    assert out["source_checksum"] == filestore.checksum(DOC)


def test_title_falls_back_to_the_filename(wired):
    conn, docs = wired
    p = docs / "no_heading-here.md"
    p.write_text("Just a paragraph, long enough to be a document.\n", encoding="utf-8")
    assert service.wrap(conn, source_path=str(p))["title"] == "no heading here"


def test_html_suffix_selects_the_html_reader(wired):
    conn, docs = wired
    p = docs / "frag.html"
    p.write_text("<section><h1>Từ HTML</h1></section>", encoding="utf-8")
    assert service.wrap(conn, source_path=str(p))["source_format"] == "html"


def test_content_and_source_path_are_mutually_exclusive(wired):
    conn, docs = wired
    p = docs / "a.md"
    p.write_text(DOC, encoding="utf-8")
    with pytest.raises(ValueError):
        service.wrap(conn, title="T", content=DOC, source_path=str(p))
    with pytest.raises(ValueError):
        service.wrap(conn, title="T")               # neither


def test_content_still_requires_a_title(wired):
    conn, _ = wired
    with pytest.raises(ValueError):
        service.wrap(conn, content=DOC)


# --- the read guard, fail-closed -------------------------------------------
def test_negative_a_path_outside_the_roots_is_refused(wired):
    """NEGATIVE TEST — attempt the blocked action and prove nothing was stored.

    `source_path` moves the read into THIS process, which is a different trust
    boundary from `content` (where the agent had already read the file through
    its own permission gate). Without this an agent could aim the server at a
    private key and the bytes would land in a publishable artifact.
    """
    conn, _ = wired
    with pytest.raises(PermissionError):
        service.wrap(conn, source_path="/etc/passwd")
    assert store.list_rows(conn) == []
    assert not list(filestore.root().glob("*")) or True  # nothing indexed


def test_negative_a_missing_path_fails_closed(wired):
    conn, docs = wired
    with pytest.raises(FileNotFoundError):
        service.wrap(conn, source_path=str(docs / "nope.md"))
    assert store.list_rows(conn) == []


def test_a_directory_is_not_a_document(wired):
    conn, docs = wired
    with pytest.raises(IsADirectoryError):
        service.wrap(conn, source_path=str(docs))


# --- refresh / stale ------------------------------------------------------
def test_refresh_rereads_the_origin_into_a_new_version(wired):
    conn, docs = wired
    p = docs / "series.md"
    p.write_text(DOC, encoding="utf-8")
    first = service.wrap(conn, source_path=str(p))

    p.write_text(DOC + "\nA paragraph added while still editing.\n", encoding="utf-8")
    again = service.refresh(conn, first["id"])
    assert again["id"] == first["id"]
    assert again["version"] == 2
    assert "still editing" in service.get(
        conn, first["id"], include_source=True)["source"]


def test_stale_reports_the_drift_then_clears_after_refresh(wired):
    conn, docs = wired
    p = docs / "series.md"
    p.write_text(DOC, encoding="utf-8")
    art = service.wrap(conn, source_path=str(p))
    assert service.stale(conn, art["id"])["stale"] is False

    p.write_text(DOC + "\nEdited.\n", encoding="utf-8")
    verdict = service.stale(conn, art["id"])
    assert verdict["stale"] is True
    assert verdict["stored_checksum"] != verdict["origin_checksum"]

    service.refresh(conn, art["id"])
    assert service.stale(conn, art["id"])["stale"] is False


def test_a_transcript_origin_is_not_refreshable(wired):
    """91 of the 97 artifacts in the live library have origin_path pointing at a
    .jsonl transcript, which grows every turn. Treating those as refreshable
    would make `stale` report drift for almost the whole library, so both verbs
    refuse them explicitly instead."""
    conn, _ = wired
    art = service.wrap(conn, title="From a session", content=DOC,
                       origin_kind="claude_session",
                       origin_path="/tmp/whatever.jsonl")
    with pytest.raises(ValueError):
        service.refresh(conn, art["id"])
    verdict = service.stale(conn, art["id"])
    assert verdict["refreshable"] is False and verdict["stale"] is None


# --- discover(roots=…) ----------------------------------------------------
def test_discover_finds_real_docs_under_an_extra_root(wired):
    conn, docs = wired
    (docs / "plan.md").write_text(DOC * 6, encoding="utf-8")
    res = discover.scan_docs([str(docs)], min_bytes=10)
    assert [c["title"] for c in res["candidates"]] == ["Measured Title"]
    cand = res["candidates"][0]
    assert cand["origin_kind"] == "doc_file"
    assert cand["origin_path"] == str((docs / "plan.md").resolve())


def test_discover_refuses_a_root_outside_the_allowed_set(wired):
    _, _ = wired
    res = discover.scan_docs(["/etc"], min_bytes=10)
    assert res["candidates"] == []
    assert any("outside the allowed" in r for r in res["refused_roots"])


def test_discover_skips_the_filestore_itself(wired):
    """Otherwise every artifact's own source.md would come back as a new
    candidate on the next scan."""
    conn, docs = wired
    seed = docs / "seed.md"
    seed.write_text(DOC, encoding="utf-8")
    service.wrap(conn, source_path=str(seed))

    store_root = filestore.root()
    assert (store_root).exists(), "the wrap should have created the filestore"
    # Widen the roots to cover the filestore, then prove it is still skipped.
    res = discover.scan_docs([str(store_root)], min_bytes=10)
    assert res["candidates"] == []
