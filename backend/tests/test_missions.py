"""Missions store: seed + the hold state-machine (create -> claim -> release -> land)."""
import sqlite3

from flightdeck.missions import store


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_seed_populates_board_and_holds():
    c = _conn()
    store.init(c)
    data = store.list_missions(c)
    assert len(data["missions"]) >= 8
    # seed includes held missions -> derived sessions are non-empty
    assert any(s["state"] == "ACTIVE" for s in data["sessions"])


def test_lifecycle_create_claim_release_land():
    c = _conn()
    store.init(c)

    m = store.create_mission(c, "Test mission", note="n", tags=["X"], status="INBOX")
    mid = m["id"]
    assert m["status"] == "INBOX" and m["hold"] is None and m["tags"] == ["X"]

    m = store.claim(c, mid, "SESS1")
    assert m["hold"]["session_id"] == "SESS1" and m["hold"]["state"] == "ACTIVE"
    assert any(s["session_id"] == "SESS1" for s in store.list_sessions(c))

    # take-over: a second session claims the same mission
    m = store.claim(c, mid, "SESS2")
    assert m["hold"]["session_id"] == "SESS2"

    m = store.release(c, mid)
    assert m["hold"] is None

    m = store.land(c, mid)
    assert m["status"] == "DONE" and m["hold"] is None

    actions = [entry["action"] for entry in store.get_mission(c, mid)["log"]]
    for expected in ("CREATED", "CLAIMED", "RELEASED", "LANDED"):
        assert expected in actions


def test_create_with_claim_on_create():
    c = _conn()
    store.init(c)
    m = store.create_mission(c, "Claimed on create", claim_session="OWNER")
    assert m["hold"] is not None and m["hold"]["session_id"] == "OWNER"


def test_kind_note_vs_todo_and_toggle():
    c = _conn()
    store.init(c)
    note = store.create_mission(c, "A memory note", kind="NOTE")
    task = store.create_mission(c, "A task")  # default TODO
    assert note["kind"] == "NOTE" and task["kind"] == "TODO"
    toggled = store.update_mission(c, note["id"], kind="TODO")
    assert toggled["kind"] == "TODO"
    # invalid kind is ignored (stays previous)
    kept = store.update_mission(c, task["id"], kind="BOGUS")
    assert kept["kind"] == "TODO"


def test_update_refreshes_beat_and_liveness():
    c = _conn()
    store.init(c)
    m = store.create_mission(c, "Work item")
    mid = m["id"]
    store.claim(c, mid, "S1")
    assert store.get_mission(c, mid)["hold"]["state"] == "ACTIVE"  # fresh beat
    # editing the note (the stand-in for "done") refreshes the beat and changes content
    m = store.update_mission(c, mid, note="note abc (done)")
    assert m["note"] == "note abc (done)" and m["hold"]["state"] == "ACTIVE"
    # a stale beat decays the state (simulate by writing an old hold_beat)
    c.execute("UPDATE missions SET hold_beat='2000-01-01T00:00:00Z' WHERE id=?", (mid,))
    assert store.get_mission(c, mid)["hold"]["state"] == "STALE"


def test_done_is_unread_until_marked_read():
    c = _conn()
    store.init(c)
    m = store.create_mission(c, "task")
    assert m["is_read"] is False
    m = store.update_mission(c, m["id"], status="DONE")  # newly done -> unread (glows)
    assert m["status"] == "DONE" and m["is_read"] is False
    m = store.mark_read(c, m["id"])
    assert m["is_read"] is True


def test_claim_identity_name():
    c = _conn()
    store.init(c)
    a = store.create_mission(c, "x")
    a = store.claim(c, a["id"], "S1", name="Icarus Quill")
    assert a["hold"]["name"] == "Icarus Quill"
    b = store.create_mission(c, "y")
    b = store.claim(c, b["id"], "S2")  # no name -> falls back to session id
    assert b["hold"]["name"] == "S2"
    assert " " in store.random_name()  # "<Name> <Word>"


def test_delete_mission():
    c = _conn()
    store.init(c)
    m = store.create_mission(c, "temp")
    assert store.delete_mission(c, m["id"]) is True
    assert store.get_mission(c, m["id"]) is None
    assert store.delete_mission(c, "m_nope") is False


def test_get_missing_returns_none():
    c = _conn()
    store.init(c)
    assert store.get_mission(c, "m_nope") is None
    assert store.claim(c, "m_nope", "S") is None
