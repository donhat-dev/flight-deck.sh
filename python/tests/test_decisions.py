"""Approval channel: escalation policy + the pending-decision store.

Mirrors `test_missions.py`'s shape (a bare in-memory sqlite connection, no
FastAPI) since `flightdeck.decisions` is pure storage + policy, same as
`flightdeck.missions.store`. The escalation-policy tests use `tmp_path` +
`FLIGHTDECK_APPROVE_CONF` rather than the operator's real
`~/.flightdeck/approve.conf`, for the same hermetic reason `conftest.py`
strips `TOKEN_AUDIT_DATABASE_URL`: a test must not depend on, or be able to
corrupt, ambient machine state.
"""
import sqlite3
import time

import pytest

from flightdeck import decisions
from flightdeck.events import BUS


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture(autouse=True)
def _reset_bus():
    # `flightdeck.decisions` emits on the module-level BUS singleton; a sink
    # registered by one test must not still be listening in the next one.
    yield
    BUS.reset()


@pytest.fixture(autouse=True)
def _isolate_approve_conf(monkeypatch, tmp_path):
    # Point every test at a throwaway path by default (no file there yet, so
    # `should_escalate` sees an empty pattern list unless a test writes one).
    # Also reset the module cache so a previous test's mtime/path pair cannot
    # leak into this one.
    monkeypatch.setenv(decisions.CONFIG_ENV_VAR, str(tmp_path / "approve.conf"))
    decisions._cache.update(path=None, mtime=None, patterns=[])
    yield


# ---------------------------------------------------------------- escalation

def test_missing_config_escalates_nothing():
    assert decisions.should_escalate("Bash", {"command": "rm -rf /tmp/x"}) is None


def test_pattern_match_returns_the_matched_line(tmp_path, monkeypatch):
    conf = tmp_path / "approve.conf"
    conf.write_text("# comment line, ignored\n\nrm\\s+-rf\n")
    monkeypatch.setenv(decisions.CONFIG_ENV_VAR, str(conf))

    matched = decisions.should_escalate("Bash", {"command": "rm -rf /tmp/x"})
    assert matched == "rm\\s+-rf"


def test_non_matching_command_does_not_escalate(tmp_path, monkeypatch):
    conf = tmp_path / "approve.conf"
    conf.write_text("rm\\s+-rf\n")
    monkeypatch.setenv(decisions.CONFIG_ENV_VAR, str(conf))

    assert decisions.should_escalate("Bash", {"command": "git status"}) is None


def test_non_bash_tool_falls_back_to_json_dump(tmp_path, monkeypatch):
    conf = tmp_path / "approve.conf"
    conf.write_text("private_key\\.bin\n")
    monkeypatch.setenv(decisions.CONFIG_ENV_VAR, str(conf))

    matched = decisions.should_escalate(
        "Write", {"file_path": "/repo/nakivo_sale/private_key.bin"})
    assert matched == "private_key\\.bin"


def test_malformed_pattern_line_is_skipped_not_fatal(tmp_path, monkeypatch):
    conf = tmp_path / "approve.conf"
    conf.write_text("rm\\s+-rf\n[unterminated(\n")  # second line is bad regex
    monkeypatch.setenv(decisions.CONFIG_ENV_VAR, str(conf))

    assert decisions.should_escalate("Bash", {"command": "rm -rf x"}) == "rm\\s+-rf"


def test_mtime_reload_picks_up_an_edit_without_a_restart(tmp_path, monkeypatch):
    conf = tmp_path / "approve.conf"
    conf.write_text("nomatch_pattern_xyz\n")
    monkeypatch.setenv(decisions.CONFIG_ENV_VAR, str(conf))

    assert decisions.should_escalate("Bash", {"command": "git push --force"}) is None

    # Edit the file; bump mtime forward so a fast filesystem clock cannot make
    # the second write land at the same second as the first.
    time.sleep(0.01)
    conf.write_text("push\\s+--force\n")
    import os
    future = os.path.getmtime(conf) + 2
    os.utime(conf, (future, future))

    matched = decisions.should_escalate("Bash", {"command": "git push --force"})
    assert matched == "push\\s+--force"


# ---------------------------------------------------------------- store

def test_create_emits_pending_and_resolve_emits_resolved():
    seen = []
    BUS.on("decision", seen.append)

    c = _conn()
    row = decisions.create(c, tool_name="Bash", command="rm -rf x",
                            session_id="S1", pattern="rm\\s+-rf", cwd="/tmp")
    assert row["state"] == "pending" and row["tool_name"] == "Bash"

    resolved = decisions.resolve(c, row["id"], action="approve")
    assert resolved["state"] == "approved" and resolved["resolved_at"]

    assert [e.kind for e in seen] == ["decision.pending", "decision.resolved"]
    assert seen[0].payload["id"] == row["id"]
    assert seen[1].payload["state"] == "approved"


def test_resolve_reject_and_edit_transitions():
    c = _conn()
    a = decisions.create(c, tool_name="Bash", command="cmd a")
    b = decisions.create(c, tool_name="Bash", command="cmd b")

    rejected = decisions.resolve(c, a["id"], action="reject", note="not now")
    assert rejected["state"] == "rejected" and rejected["note"] == "not now"

    edited = decisions.resolve(c, b["id"], action="edit", edited_command="cmd b --safe")
    assert edited["state"] == "edited"
    assert edited["edited_command"] == "cmd b --safe"


def test_resolve_unknown_id_returns_none():
    c = _conn()
    assert decisions.resolve(c, "nope", action="approve") is None


def test_double_resolve_raises():
    c = _conn()
    row = decisions.create(c, tool_name="Bash", command="cmd")
    decisions.resolve(c, row["id"], action="approve")
    with pytest.raises(decisions.AlreadyResolvedError):
        decisions.resolve(c, row["id"], action="reject")


def test_list_pending_excludes_resolved():
    c = _conn()
    a = decisions.create(c, tool_name="Bash", command="cmd a")
    decisions.create(c, tool_name="Bash", command="cmd b")
    decisions.resolve(c, a["id"], action="approve")

    pending = decisions.list_pending(c)
    assert len(pending) == 1 and pending[0]["command"] == "cmd b"


def test_get_missing_returns_none():
    c = _conn()
    assert decisions.get(c, "nope") is None


def test_auto_release_on_read_past_the_timeout(monkeypatch):
    c = _conn()
    row = decisions.create(c, tool_name="Bash", command="cmd")
    # Backdate created_at past the fail-open window rather than sleeping 90s.
    c.execute("UPDATE decisions SET created_at='2000-01-01T00:00:00Z' WHERE id=?",
              (row["id"],))
    c.commit()

    seen = []
    BUS.on("decision.resolved", seen.append)

    refetched = decisions.get(c, row["id"])
    assert refetched["state"] == "auto_released"
    assert refetched["resolved_at"]
    assert len(seen) == 1 and seen[0].source == "hook-timeout"

    # A second read must not double-emit: the row is no longer pending.
    decisions.get(c, row["id"])
    assert len(seen) == 1


def test_auto_release_also_fires_from_list_pending():
    c = _conn()
    row = decisions.create(c, tool_name="Bash", command="cmd")
    c.execute("UPDATE decisions SET created_at='2000-01-01T00:00:00Z' WHERE id=?",
              (row["id"],))
    c.commit()

    assert decisions.list_pending(c) == []
    assert decisions.get(c, row["id"])["state"] == "auto_released"
