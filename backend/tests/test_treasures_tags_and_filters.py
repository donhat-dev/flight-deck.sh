"""Tags, the origin-root filter, and the conditional refresh."""
import pytest

from flightdeck import db
from flightdeck.treasures import service, store

DOC = "# A Doc\n\nBody long enough to be a document.\n"


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    monkeypatch.setenv("TREASURES_READ_ROOTS", str(docs))
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn, docs


# --- tags -------------------------------------------------------------------
def test_tags_are_normalised_and_deduped(wired):
    conn, _ = wired
    art = service.wrap(conn, title="T", content=DOC)
    out = service.set_tags(conn, art["id"], ["  Billing ", "billing", "CRM"])
    assert out["tags"] == ["billing", "crm"]     # lowercased, deduped, sorted


def test_add_and_remove_leave_the_rest_alone(wired):
    conn, _ = wired
    art = service.wrap(conn, title="T", content=DOC)
    service.set_tags(conn, art["id"], ["billing", "crm"])
    assert service.tag(conn, art["id"], add=["odoo"])["tags"] == \
        ["billing", "crm", "odoo"]
    assert service.tag(conn, art["id"], remove=["crm"])["tags"] == \
        ["billing", "odoo"]


def test_adding_a_tag_twice_is_idempotent(wired):
    """The composite primary key would otherwise raise on the second insert."""
    conn, _ = wired
    art = service.wrap(conn, title="T", content=DOC)
    service.tag(conn, art["id"], add=["billing"])
    assert service.tag(conn, art["id"], add=["billing"])["tags"] == ["billing"]


def test_filtering_by_tag(wired):
    conn, _ = wired
    a = service.wrap(conn, title="A", content=DOC)
    b = service.wrap(conn, title="B", content=DOC + "\ndiffer\n")
    service.set_tags(conn, a["id"], ["billing"])
    service.set_tags(conn, b["id"], ["hr"])
    assert [r["id"] for r in service.list_rows(conn, tag="billing")] == [a["id"]]
    # normalised on the way in, so a differently-cased filter still matches
    assert [r["id"] for r in service.list_rows(conn, tag="BILLING")] == [a["id"]]


def test_rows_carry_their_tags_and_all_tags_counts(wired):
    conn, _ = wired
    a = service.wrap(conn, title="A", content=DOC)
    b = service.wrap(conn, title="B", content=DOC + "\ndiffer\n")
    service.set_tags(conn, a["id"], ["billing", "odoo"])
    service.set_tags(conn, b["id"], ["billing"])
    rows = {r["id"]: r["tags"] for r in service.list_rows(conn)}
    assert rows[a["id"]] == ["billing", "odoo"]
    assert service.get(conn, a["id"])["tags"] == ["billing", "odoo"]
    assert store.all_tags(conn) == [{"tag": "billing", "count": 2},
                                    {"tag": "odoo", "count": 1}]


def test_deleting_an_artifact_takes_its_tags(wired):
    """SQLite does not enforce the FK's ON DELETE CASCADE, so the delete path
    removes tags explicitly — otherwise they would outlive the artifact and be
    inherited by the next id that happened to collide."""
    conn, _ = wired
    art = service.wrap(conn, title="T", content=DOC)
    service.set_tags(conn, art["id"], ["billing"])
    service.delete(conn, art["id"])
    assert store.all_tags(conn) == []


# --- origin_root filter -----------------------------------------------------
def test_origin_root_matches_a_folder_prefix(wired):
    conn, docs = wired
    top = docs / "top.md"
    nested = docs / "sub" / "nested.md"
    top.write_text(DOC, encoding="utf-8")
    nested.write_text(DOC + "\ndiffer\n", encoding="utf-8")
    service.wrap(conn, source_path=str(top))
    service.wrap(conn, source_path=str(nested))

    assert len(service.list_rows(conn, origin_root=str(docs))) == 2
    only_sub = service.list_rows(conn, origin_root=str(docs / "sub"))
    assert [r["origin_path"] for r in only_sub] == [str(nested.resolve())]
    assert service.list_rows(conn, origin_root="/nowhere") == []


def test_origin_root_escapes_like_wildcards(wired):
    """`_` matches any character in LIKE, so an unescaped root would over-match:
    `report_v2` would also select `reportXv2`."""
    conn, docs = wired
    real = docs / "report_v2.md"
    decoy = docs / "reportXv2.md"
    real.write_text(DOC, encoding="utf-8")
    decoy.write_text(DOC + "\ndiffer\n", encoding="utf-8")
    service.wrap(conn, source_path=str(real))
    service.wrap(conn, source_path=str(decoy))

    hits = service.list_rows(conn, origin_root=str(docs / "report_v2"))
    assert [r["origin_path"] for r in hits] == [str(real.resolve())]


def test_origin_root_works_for_a_url_prefix(wired):
    """A URL origin is just another origin_path prefix."""
    conn, _ = wired
    service.wrap(conn, title="Ported", content=DOC,
                 origin_kind="artifact-port",
                 origin_path="https://claude.ai/code/artifact/abc")
    assert len(service.list_rows(conn, origin_root="https://claude.ai/")) == 1
    assert service.list_rows(conn, origin_root="https://example.com/") == []


# --- conditional refresh ----------------------------------------------------
def test_refresh_skips_when_the_origin_is_unchanged(wired):
    """The point of the condition: re-reading an identical file must not mint a
    version, or a watcher-driven flow would fill the history with duplicates."""
    conn, docs = wired
    p = docs / "series.md"
    p.write_text(DOC, encoding="utf-8")
    art = service.wrap(conn, source_path=str(p))

    out = service.refresh(conn, art["id"])
    assert out["skipped"] is True
    assert out["version"] == 1
    assert service.get(conn, art["id"])["version"] == 1


def test_refresh_versions_when_the_origin_differs(wired):
    conn, docs = wired
    p = docs / "series.md"
    p.write_text(DOC, encoding="utf-8")
    art = service.wrap(conn, source_path=str(p))
    p.write_text(DOC + "\nEdited.\n", encoding="utf-8")

    out = service.refresh(conn, art["id"])
    assert out.get("skipped") is None
    assert out["version"] == 2


def test_force_versions_even_when_unchanged(wired):
    conn, docs = wired
    p = docs / "series.md"
    p.write_text(DOC, encoding="utf-8")
    art = service.wrap(conn, source_path=str(p))
    out = service.refresh(conn, art["id"], force=True)
    assert out.get("skipped") is None
    assert out["version"] == 2
