"""Keep the test suite hermetic.

The suite is written against SQLite: every test builds its own database under
`tmp_path`. But `flightdeck.db` picks its engine from configuration, and
`config.load()` reads `TOKEN_AUDIT_DATABASE_URL` from the environment. Anyone
who has sourced the repo's `.env` (which `make dev` / `make serve` and the
systemd unit all do) therefore had that variable exported — and the suite would
silently run against the **production PostgreSQL**, writing rows into the real
library. That happened once: two rows pointing at `/tmp/pytest-of-*` had to be
deleted by hand from production.

So, for every test:
  - the variable is removed from the environment, and
  - `flightdeck.db`'s module-level engine state is reset, so a test that
    deliberately configures an engine cannot leak it into the next one.

A test that genuinely wants PostgreSQL must configure it explicitly inside the
test; ambient environment is never enough.
"""
import pytest

from flightdeck import db

_ENV_KEYS = ("TOKEN_AUDIT_DATABASE_URL",)


@pytest.fixture(autouse=True)
def _hermetic_sqlite(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    # Reset the engine seam itself: `configure()` caches the DSN and the pool at
    # module level, and `connect()/open_read()/open_write()` branch on it.
    monkeypatch.setattr(db, "_URL", None, raising=False)
    monkeypatch.setattr(db, "_POOL", None, raising=False)

    # The MCP server caches its own connection + cfg; a leftover from a previous
    # test would otherwise be reused with the wrong engine.
    try:
        from flightdeck.treasures import mcp_server
    except ImportError:                      # module not present in some runs
        pass
    else:
        monkeypatch.setitem(mcp_server._state, "cfg", None)
        monkeypatch.setitem(mcp_server._state, "conn", None)
    yield
