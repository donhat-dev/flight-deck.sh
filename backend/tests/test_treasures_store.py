from flightdeck import db
from flightdeck.treasures import store


def _row(**over):
    row = {
        "id": "abc123def456",
        "title": "Helpdesk OSS alternatives",
        "slug": "helpdesk-oss-alternatives",
        "dir_path": "/tmp/store/helpdesk-oss-alternatives-abc123def456",
        "kind": "report",
        "language": "en",
        "status": "draft",
        "version": 1,
        "source_format": "markdown",
        "source_checksum": "s" * 64,
        "render_checksum": "r" * 64,
        "render_bytes": 12345,
        "origin_kind": "claude_session",
        "origin_id": "560cf395-75cc-4ef7-bfb2-b1d56dd78a58",
        "origin_path": None,
        "published_url": None,
        "duplicate_of": None,
        "authored_at": "2026-07-21T10:00:00+00:00",
        "ingested_at": "2026-07-21T10:00:05+00:00",
        "updated_at": "2026-07-21T10:00:05+00:00",
    }
    row.update(over)
    return row


def test_upsert_then_get_by_id_and_slug(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.upsert(conn, _row())
    by_id = store.get(conn, "abc123def456")
    by_slug = store.get(conn, "helpdesk-oss-alternatives")
    assert by_id["title"] == "Helpdesk OSS alternatives"
    assert by_slug["id"] == "abc123def456"
    assert by_id["version"] == 1
    assert store.get(conn, "nope") is None


def test_upsert_is_idempotent_and_updates(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.upsert(conn, _row())
    store.upsert(conn, _row(version=2, status="published",
                            published_url="https://claude.ai/public/artifacts/x"))
    rows = store.list_rows(conn)
    assert len(rows) == 1                      # same id -> one row
    assert rows[0]["version"] == 2
    assert rows[0]["status"] == "published"


def test_list_filters_and_search(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.upsert(conn, _row())
    store.upsert(conn, _row(id="vn0000000001", slug="bao-cao-helpdesk",
                            title="Bao cao helpdesk", language="vi",
                            dir_path="/tmp/store/bao-cao-helpdesk-vn0000000001"))
    assert len(store.list_rows(conn, language="vi")) == 1
    assert len(store.list_rows(conn, status="draft")) == 2
    assert len(store.list_rows(conn, origin_id="560cf395-75cc-4ef7-bfb2-b1d56dd78a58")) == 2
    assert len(store.list_rows(conn, kind="report")) == 2
    assert store.list_rows(conn, query="bao cao")[0]["language"] == "vi"
    assert len(store.list_rows(conn, limit=1)) == 1


def test_init_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.init(conn)                            # must not raise
    assert store.list_rows(conn) == []
