"""The one copy of the agent-tool runtime.

The radar and treasures MCP servers each carried this whole file inline — seven
functions, name-for-name identical, ~130 lines twice — and a third domain would have
copied it again. Everything here is exactly what those two files had, kept as ONE copy
so a fix (the idle reaper, the commit-after-every-call rule) lands everywhere at once.

Two lifecycles, one module:

  MCP (long-lived)   `configure()` once, `conn()` per call, the reaper returns a parked
                     session to ZERO connections. Measured before the reaper existed:
                     ~12 idle PostgreSQL connections across parked sessions.
  CLI (per-call)     `configure(reaper=False)` — the process exits in under a second,
                     so a reclaim thread would be a thread that never fires.
"""
import os
import threading
import time
from pathlib import Path

# backend/, resolved from this file so every entrypoint works from any cwd.
BACKEND = Path(__file__).resolve().parents[2]

_state = {"cfg": None, "conn": None, "used_at": 0.0}

_IDLE_TTL = float(
    os.environ.get("FLIGHTDECK_CONN_IDLE_TTL")
    # The two names the standalone servers used, honoured so an operator who tuned one
    # does not silently lose the tuning in the consolidation.
    or os.environ.get("RADAR_CONN_IDLE_TTL")
    or os.environ.get("TREASURES_CONN_IDLE_TTL")
    or "180")
_REAP_EVERY = 20.0
_lock = threading.RLock()


def _load_dotenv() -> None:
    """systemd injects .env for the web app; a standalone tool gets no such help."""
    env_path = BACKEND.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def configure(cfg: dict | None = None, *, reaper: bool = True) -> None:
    """Wire config and BOTH domains' schemas once. Tests pass an explicit cfg.

    Both schemas on purpose: the surface serves every domain, so a scratch database
    handed to one domain's tests quietly grows the other's tables too — both inits are
    idempotent, and a single `configure` that works everywhere beats two that each work
    somewhere.
    """
    from flightdeck import config, db
    from flightdeck.radar import store as radar_store
    from flightdeck.treasures import store as treasures_store

    if cfg is None:
        _load_dotenv()
        cfg = config.load(os.environ.get(
            "TOKEN_AUDIT_CONFIG", str(BACKEND / "config.toml")))
    db.configure(cfg)
    c = db.open_write(cfg["db_path"])
    try:
        radar_store.init(c)
        treasures_store.init(c)
    finally:
        c.close()
    with _lock:
        _state["cfg"] = cfg
        _state["conn"] = None
        _state["used_at"] = 0.0
    if reaper:
        _start_reaper()


def conn():
    """The write connection, opened on demand and kept only while in use."""
    from flightdeck import db
    with _lock:
        if _state["cfg"] is None:
            configure()
        if _state["conn"] is None:
            _state["conn"] = db.open_write(_state["cfg"]["db_path"])
        _state["used_at"] = time.monotonic()
        return _state["conn"]


def commit_quietly() -> None:
    """Commit after EVERY tool call, read or write.

    The write connection is not autocommit, so a read-only call would otherwise leave
    the session "idle in transaction" holding a lock — a stray treasure_get once blocked
    an unrelated ALTER TABLE for 14+ hours.
    """
    if _state["conn"] is not None:
        try:
            _state["conn"].commit()
        except Exception:
            pass


def release_idle(ttl: float = None) -> bool:
    """Close the write connection if unused for `ttl` seconds. True when it closed."""
    limit = _IDLE_TTL if ttl is None else ttl
    with _lock:
        c = _state["conn"]
        if c is None or time.monotonic() - _state["used_at"] < limit:
            return False
        _state["conn"] = None
        try:
            c.close()
        except Exception:
            pass
        return True


def _start_reaper() -> None:
    if _state.get("reaper"):
        return

    def loop():
        while True:
            time.sleep(_REAP_EVERY)
            try:
                release_idle()
            except Exception:
                pass

    thread = threading.Thread(target=loop, name="agentsurface-conn-reaper", daemon=True)
    _state["reaper"] = thread
    thread.start()
