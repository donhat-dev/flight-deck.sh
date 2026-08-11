"""The agent surface's write connection is reclaimed while idle.

Retargeted at flightdeck.agentsurface.runtime when the lifecycle moved there in the
CLI/MCP consolidation — these tests own the lifecycle, and the treasures module now
only re-exports it. The treasures wrap/get calls stay, as proof the reclaim is safe
under a real domain's traffic and not only in the abstract.

Why this exists: an MCP server lives as long as its Claude Code session, which can
be days. It used to hold its write connection for that whole time, so a machine
with ~12 open sessions carried 12 idle PostgreSQL connections doing nothing. That
was never a leak — every process had a live `claude` parent — but the cost grew
with every session and never shrank.
"""
import time

import pytest

from flightdeck.agentsurface import runtime
from flightdeck.treasures import service

DOC = "# Conn\n\nBody long enough to be a document.\n"


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    runtime.configure({"db_path": str(tmp_path / "t.db"), "database_url": None,
                          "projects_dir": str(tmp_path / "projects")})
    yield
    runtime.release_idle(0)


def test_configure_leaves_no_connection_open(wired):
    """Schema work needs a connection, not a lasting one."""
    assert runtime._state["conn"] is None


def test_a_call_opens_one_and_reuses_it(wired):
    first = runtime.conn()
    assert first is not None
    assert runtime.conn() is first, "each call must not open a new connection"


def test_idle_connection_is_released(wired):
    runtime.conn()
    assert runtime._state["conn"] is not None

    # Not yet idle: a long TTL must NOT reclaim a connection just used.
    assert runtime.release_idle(60) is False
    assert runtime._state["conn"] is not None

    # Idle past the limit: reclaimed.
    time.sleep(0.05)
    assert runtime.release_idle(0.01) is True
    assert runtime._state["conn"] is None


def test_work_still_succeeds_after_a_release(wired):
    """The reclaim is only safe if the next call transparently reopens."""
    art = service.wrap(runtime.conn(), title="Before", content=DOC)
    assert runtime.release_idle(0) is True

    # A fresh connection, and the row written before the release is still there.
    row = service.get(runtime.conn(), art["id"])
    assert row["title"] == "Before"

    again = service.wrap(runtime.conn(), title="After", content=DOC + "\nmore\n")
    assert again["id"] != art["id"]


def test_release_is_idempotent_and_safe_when_nothing_is_open(wired):
    assert runtime.release_idle(0) is False   # nothing open yet
    runtime.conn()
    assert runtime.release_idle(0) is True
    assert runtime.release_idle(0) is False   # already reclaimed


def test_release_survives_a_connection_that_cannot_close(wired):
    """A connection the server can no longer close is already useless. Raising
    here would take the MCP server down on a broken socket, so the failure is
    swallowed and the slot cleared for a fresh one.

    The stub goes straight into the slot because sqlite3.Connection.close is
    read-only and cannot be monkeypatched."""
    class Broken:
        def close(self):
            raise RuntimeError("socket already gone")

    runtime.conn()
    with runtime._lock:
        runtime._state["conn"] = Broken()

    assert runtime.release_idle(0) is True
    assert runtime._state["conn"] is None
    assert runtime.conn() is not None       # recovers


def test_the_reaper_thread_actually_reclaims_without_being_asked(monkeypatch, tmp_path):
    """The tests above call release_idle() directly, which proves the reclaim logic
    but NOT that anything ever calls it. A parked session never calls again, so if
    the timer does not fire the connection is held forever — the whole point of the
    change. This drives the real thread with a short period.
    """
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(runtime, "_IDLE_TTL", 0.05)
    monkeypatch.setattr(runtime, "_REAP_EVERY", 0.05)
    # A fresh reaper for this test's timings, not whichever one an earlier test left.
    runtime._state["reaper"] = None
    runtime.configure({"db_path": str(tmp_path / "r.db"), "database_url": None,
                          "projects_dir": str(tmp_path / "projects")})

    runtime.conn()
    assert runtime._state["conn"] is not None

    deadline = time.monotonic() + 3
    while runtime._state["conn"] is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert runtime._state["conn"] is None, "the reaper thread never fired"
