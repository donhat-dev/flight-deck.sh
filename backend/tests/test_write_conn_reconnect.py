"""The long-lived write connection must survive Postgres restarting.

What happened: Postgres was shut down at 09:57:54 and `psycopg.errors.AdminShutdown`
killed every open connection. Reads recovered on their own, because they come from
a `psycopg_pool.ConnectionPool` that discards dead connections and opens new ones.
Writes did not: `open_write()` handed back one raw connection at startup, held for
the process lifetime by `Runtime` and by `app.state.write_conn`. Once dead it
stayed dead — 2,608 `OperationalError: the connection is closed` over 26 hours,
with ingest frozen and the missions/hub write endpoints broken too, while systemd
still reported `active (running)` because the process was alive.

Retry-once is safe here specifically because the ledger is rebuildable: messages /
files / tool_calls are UNLOGGED in Postgres precisely because they are derived from
the JSONL and re-ingested on the next pass. A transaction lost with the dead
connection was already lost; reconnecting is strictly better than failing forever.
"""
import psycopg
import pytest

from flightdeck import db


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    """Stand-in for a psycopg connection with a scripted failure."""

    def __init__(self, fail_with=None, label=""):
        self.fail_with = fail_with
        self.label = label
        self.statements = []
        self.commits = 0
        self.closed = False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if self.fail_with is not None:
            raise self.fail_with
        return FakeCursor([(self.label,)])

    def commit(self):
        if self.fail_with is not None:
            raise self.fail_with
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.fixture()
def pg(monkeypatch):
    """Make db believe it is on Postgres, and script what _pg_connect hands out."""
    monkeypatch.setattr(db, "_URL", "postgresql://fake/fake")
    made = []

    def factory(queue):
        def _pg_connect(autocommit):
            conn = queue.pop(0)
            made.append((conn, autocommit))
            return conn
        return _pg_connect

    return {"made": made, "install": lambda q: monkeypatch.setattr(
        db, "_pg_connect", factory(q))}


# ------------------------------------------------------- the failure we suffered

@pytest.mark.parametrize("boom", [
    psycopg.OperationalError("the connection is closed"),
    psycopg.errors.AdminShutdown("terminating connection due to administrator command"),
    psycopg.InterfaceError("connection already closed"),
])
def test_a_dead_connection_is_replaced_and_the_statement_runs(pg, boom):
    dead, fresh = FakeConn(fail_with=boom, label="dead"), FakeConn(label="fresh")
    pg["install"]([dead, fresh])

    conn = db.open_write("unused")
    rows = conn.execute("SELECT 1").fetchall()

    assert rows == [("fresh",)], "the statement must actually run, not just not raise"
    assert fresh.statements == ["SELECT 1"]
    assert dead.closed, "the dead connection must be released, not leaked"
    assert len(pg["made"]) == 2
    # The write connection is explicit-commit; a reconnect that silently switched
    # to autocommit would change transaction semantics under the caller.
    assert [autocommit for _, autocommit in pg["made"]] == [False, False]


def test_commit_reconnects_too(pg):
    dead = FakeConn(fail_with=psycopg.OperationalError("gone"), label="dead")
    fresh = FakeConn(label="fresh")
    pg["install"]([dead, fresh])

    conn = db.open_write("unused")
    conn.commit()
    assert fresh.commits == 1


def test_the_retry_happens_once_not_forever(pg):
    # A server that is genuinely down must surface the error rather than spin.
    # The old code failed 2,608 times in silence; a retry loop would just make
    # that hotter.
    boom = psycopg.OperationalError("still down")
    conns = [FakeConn(fail_with=boom, label=str(i)) for i in range(5)]
    pg["install"](conns)

    conn = db.open_write("unused")
    with pytest.raises(psycopg.OperationalError):
        conn.execute("SELECT 1")
    assert len(pg["made"]) == 2, "exactly one reconnect attempt"


# ------------------------------------------------------------ what must NOT retry

def test_a_real_sql_error_is_not_retried(pg):
    # Retrying a genuine SQL fault would hide a bug behind a reconnect and
    # double every failing statement.
    bad = FakeConn(fail_with=psycopg.ProgrammingError('relation "nope" does not exist'))
    pg["install"]([bad, FakeConn(label="unused")])

    conn = db.open_write("unused")
    with pytest.raises(psycopg.ProgrammingError):
        conn.execute("SELECT * FROM nope")
    assert len(pg["made"]) == 1, "no reconnect on a SQL error"


def test_reconnect_failure_surfaces_the_original_error(pg):
    # If the replacement connection cannot be opened either, the caller must see
    # a database error — not a TypeError from the retry path.
    dead = FakeConn(fail_with=psycopg.OperationalError("connection is closed"))

    def refuse(autocommit):
        raise psycopg.OperationalError("Connection refused")

    pg["install"]([dead])
    import pytest as _pytest
    monkey = _pytest.MonkeyPatch()
    monkey.setattr(db, "_pg_connect", refuse)
    try:
        conn = db._PgWriteConn(dead)
        with pytest.raises(psycopg.OperationalError, match="Connection refused"):
            conn.execute("SELECT 1")
    finally:
        monkey.undo()


# --------------------------------------------------------------- sqlite untouched

def test_sqlite_write_path_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_URL", None)
    conn = db.open_write(str(tmp_path / "t.db"))
    try:
        assert not isinstance(conn, db._PgWriteConn)
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        assert conn.execute("SELECT a FROM t").fetchall()[0][0] == 1
    finally:
        conn.close()
