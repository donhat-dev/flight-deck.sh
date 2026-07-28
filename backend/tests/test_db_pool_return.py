"""A pooled connection must go back to the pool on every exit path.

REGRESSION. `_PgConn` wraps a psycopg connection and delegates unknown
attributes to it. That delegation made `with open_read(...) as conn:` silently
resolve to the WRAPPED connection's context manager, which commits and leaves
the connection checked out — so the pool (max_size 8) died after eight uses and
every request then failed with PoolTimeout. It took a watcher that ran on every
debounced ping to expose it; nothing in the request path used `with` on a pooled
connection, so the leak was invisible until the app stopped answering.
"""
from flightdeck import db


class _FakeCursor:
    def __init__(self):
        self.description = []

    def fetchone(self):
        return None


class _FakeConn:
    """Stands in for psycopg's connection, including its own context manager —
    which is exactly the trap: it exists, so `with` would find it."""

    def __init__(self):
        self.entered = False
        self.committed = False
        self.closed = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.committed = True
        return False

    def execute(self, *a, **k):
        return _FakeCursor()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class _FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.returned = []

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        self.returned.append(conn)


def test_with_returns_a_pooled_connection_to_the_pool():
    raw = _FakeConn()
    pool = _FakePool(raw)
    with db._PgConn(raw, pool) as conn:
        conn.execute("SELECT 1")
    assert pool.returned == [raw], "connection was not handed back to the pool"
    assert not raw.entered, "`with` must not fall through to the wrapped connection"


def test_with_returns_the_wrapper_not_the_inner_connection():
    """`as conn` has to be the wrapper, or the caller loses the ?->%s
    placeholder translation and every query breaks on PostgreSQL."""
    raw = _FakeConn()
    wrapper = db._PgConn(raw, _FakePool(raw))
    with wrapper as conn:
        assert conn is wrapper


def test_with_returns_the_connection_even_when_the_body_raises():
    raw = _FakeConn()
    pool = _FakePool(raw)
    try:
        with db._PgConn(raw, pool):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert pool.returned == [raw], "an exception must not leak the connection"


def test_an_unpooled_connection_is_closed_instead():
    raw = _FakeConn()
    with db._PgConn(raw, None):
        pass
    assert raw.closed is True
