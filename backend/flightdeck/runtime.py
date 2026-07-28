"""Runtime / infrastructure for the FlightDeck backend (extracted from the
`create_app()` mega-factory).

Single-process, 1-worker by design (see docs/host-stack-migration.md): the
in-process snapshot cache (`app.state.snap`), the module-level `asyncio.Event`
SSE fan-out (`_updated`), and the watcher/poll threads all assume ONE process.
Nothing here is multi-worker or async-converted.

`Runtime` owns the long-lived write connection + the ingest/snapshot machinery;
`lifespan` wires the watcher + periodic loops. Endpoint routers read shared
state via `request.app.state.*` and the `cached()` helper below.
"""
import asyncio
import os
import threading
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from flightdeck import db, ingest, metrics, rtk, usage_poll
from flightdeck.treasures import filestore

# Module-level SSE fan-out event: set by the ingest paths / poll loops, awaited
# by the /api/stream generator. Module-level on purpose (1-worker assumption).
_updated = asyncio.Event()

_RANGES = ("today", "7d", "30d", "all")
DEBOUNCE_SECONDS = 2.0
# The Treasures filestore watch debounces separately (and much shorter): a
# `wrap`/`update`/`link` writes several files in a burst (source, artifact.html,
# meta.json, ...) and this only needs to coalesce that into ~one SSE ping, not
# drive a re-ingest.
TREASURES_DEBOUNCE_SECONDS = 1.0


def since(range_key: str):
    """Translate a UI range into an ISO date cutoff (calendar-based). None = all."""
    from datetime import date, timedelta
    today = date.today()
    if range_key == "today":
        return today.isoformat()
    if range_key == "7d":
        return (today - timedelta(days=6)).isoformat()
    if range_key == "30d":
        return (today - timedelta(days=29)).isoformat()
    return None  # "all" or unknown


def cached(app: FastAPI, range_key: str, kind: str):
    """Return the precomputed snapshot payload if warm, else None. Reads
    `app.state.snap` (the in-process cache the routers depend on)."""
    snap = app.state.snap
    if snap and range_key in snap:
        return snap[range_key][kind]
    return None


class Runtime:
    """Holds the long-lived write connection and the ingest/snapshot machinery.

    A single instance lives on `app.state.runtime`. `build_snapshot` reads and
    `reingest`/`reingest_paths` write `app.state.snap` + `app.state.rtk_savings`
    (so request handlers stay pure dict lookups against the cache)."""

    def __init__(self, app: FastAPI, cfg: dict, write_conn):
        self.app = app
        self.cfg = cfg
        # Dedicated long-lived WRITE connection, used ONLY by the watcher/ingest
        # and serialized by `lock` so overlapping watcher events never run ingest
        # concurrently on it.
        self.write_conn = write_conn
        self.lock = threading.Lock()
        self.debounce_lock = threading.Lock()
        self.debounce_timer: dict = {"timer": None}
        # Changed .jsonl paths collected by the watcher between debounced flushes,
        # so a flush ingests only what changed (delta) instead of re-globbing.
        self.pending_paths: set = set()
        self.pending_lock = threading.Lock()
        # Debounce state for the Treasures filestore watch (separate from the
        # transcript watch above — it only ever fires an SSE ping, never an
        # ingest, so it needs none of the pending_paths delta bookkeeping).
        self.treasures_debounce_lock = threading.Lock()
        self.treasures_debounce_timer: dict = {"timer": None}

    def read_conn(self):
        # Fresh short-lived connection, owned by exactly one thread; caller
        # closes it. Connection creation lives in db.open_read so the engine
        # swap (SQLite -> Postgres) is a single-file change.
        return db.open_read(self.cfg["db_path"])

    def build_snapshot(self):
        # Precompute every endpoint payload for each range ONCE per ingest, so
        # request handlers are pure dict lookups (no per-request DB scan / compute
        # / GIL contention with the watcher). One read connection for all computes.
        c = self.read_conn()
        try:
            snap = {}
            for rng in _RANGES:
                s = since(rng)
                snap[rng] = {
                    "summary": metrics.summary(
                        c, self.cfg["subscription_monthly_usd"],
                        self.app.state.rtk_savings, since=s),
                    "daily": metrics.daily(c, since=s),
                    "sessions": metrics.sessions(
                        c, 100, 0, since=s, projects_dir=self.cfg["projects_dir"]),
                    "by_model": metrics.by_model(c, since=s),
                }
            return snap
        finally:
            c.close()

    def reingest(self):
        with self.lock:
            ingest.ingest_all(self.write_conn, self.cfg["projects_dir"])
        self.app.state.rtk_savings = rtk.rtk_savings_usd()
        self.app.state.snap = self.build_snapshot()   # refresh cache atomically

    def reingest_paths(self, paths):
        # Delta reingest: only the files the watcher saw change, then rebuild the
        # snapshot. Avoids the full-tree glob + per-file stat storm on every
        # jsonl append (the main load amplifier over the slow mount).
        with self.lock:
            ingest.ingest_paths(self.write_conn, paths)
        self.app.state.rtk_savings = rtk.rtk_savings_usd()
        self.app.state.snap = self.build_snapshot()

    def poll_once(self, notify=False):
        """Force one `claude -p /usage` poll; write it to the RW local-report path.

        Returns True if a fresh report was captured. Safe no-op returning False
        when the claude CLI is absent or unauthenticated (callers then fall back
        to whatever files already exist)."""
        try:
            rep = usage_poll.poll(timeout=45)
        except Exception:
            rep = None
        if not rep:
            return False
        try:
            usage_poll.write_report(
                rep, path=os.environ.get("TOKEN_AUDIT_LOCAL_REPORT_FILE"))
        except Exception:
            return False
        if notify and getattr(self.app.state, "loop", None) is not None:
            self.app.state.loop.call_soon_threadsafe(_updated.set)
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt: Runtime = app.state.runtime
    rt.reingest()
    loop = asyncio.get_event_loop()
    app.state.loop = loop
    observer = None

    # Raise the sync threadpool ceiling (anyio default is 40). Under a burst
    # of concurrent reads over a slow mount, 40 blocked workers exhausted the
    # pool and wedged every request; a higher ceiling absorbs bursts.
    try:
        import anyio
        anyio.to_thread.current_default_thread_limiter().total_tokens = int(
            os.environ.get("TOKEN_AUDIT_THREADPOOL", "100"))
    except Exception:
        traceback.print_exc()

    def _debounced_reingest():
        with rt.pending_lock:
            batch = list(rt.pending_paths)
            rt.pending_paths.clear()
        if batch:
            rt.reingest_paths(batch)   # delta: only changed files
        else:
            rt.reingest()              # safety fallback (no paths captured)
        loop.call_soon_threadsafe(_updated.set)

    def _schedule_reingest():
        with rt.debounce_lock:
            existing = rt.debounce_timer["timer"]
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, _debounced_reingest)
            timer.daemon = True
            rt.debounce_timer["timer"] = timer
            timer.start()

    # Watcher can be disabled (TOKEN_AUDIT_WATCH=0) to rely on the periodic
    # sweep + manual /api/reingest only - useful when the mount is slow and
    # constant inotify churn from many active sessions is the problem.
    watch_enabled = (os.environ.get("TOKEN_AUDIT_WATCH", "1") or "1") != "0"
    if watch_enabled:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    p = str(event.src_path)
                    if p.endswith(".jsonl"):
                        with rt.pending_lock:
                            rt.pending_paths.add(p)
                        _schedule_reingest()

            observer = Observer()
            observer.schedule(Handler(), rt.cfg["projects_dir"], recursive=True)
            observer.daemon = True
            observer.start()
        except Exception:
            observer = None
    app.state.observer = observer

    # Second, independent watchdog watch: the Treasures filestore root (NOT
    # the transcript tree — a wrap/update/link there never re-ingests). It
    # only debounces a burst of writes (a wrapped artifact touches source,
    # artifact.html, and meta.json together) down to roughly one fire, then
    # sets the SAME `_updated` event the /api/stream SSE endpoint already
    # awaits. `summary-updated` is a generic "something changed" ping — the
    # Treasures dashboard view consumes it too, alongside the usage-ledger
    # refresh it was originally built for, so it just refetches its list.
    treasures_observer = None
    try:
        from watchdog.observers import Observer as _Observer
        from watchdog.events import FileSystemEventHandler as _Handler

        froot = filestore.root()
        froot.mkdir(parents=True, exist_ok=True)

        def _treasures_ping():
            # Re-aim the origin watch first: a just-wrapped artifact brings a new
            # origin file that nothing is watching yet.
            rebuild = getattr(rt, "rebuild_origin_watch", None)
            if rebuild is not None:
                try:
                    rebuild()
                except Exception as e:
                    print(f"[treasures] origin watch rebuild failed: {e}")
            loop.call_soon_threadsafe(_updated.set)

        def _schedule_treasures_ping():
            with rt.treasures_debounce_lock:
                existing = rt.treasures_debounce_timer["timer"]
                if existing is not None:
                    existing.cancel()
                timer = threading.Timer(TREASURES_DEBOUNCE_SECONDS, _treasures_ping)
                timer.daemon = True
                rt.treasures_debounce_timer["timer"] = timer
                timer.start()

        # --- origin watch -------------------------------------------------
        # Also watch the SOURCE documents artifacts were wrapped from, so a
        # `Subscription_Service/18-….md` edited in an editor makes the dashboard
        # show its "origin changed" badge without a manual page reload.
        #
        # It only ever PINGS. Auto-refreshing on save would mint a new version
        # per keystroke-flush — a 671-line doc under editor autosave would leave
        # dozens of ~196KB version pairs an hour and make the history
        # meaningless. Staleness stays derived (computed on read) and updating
        # stays a deliberate click.
        #
        # Directories are watched, not files: editors save by write-and-rename,
        # which drops an inode-level file watch. Events are filtered back down to
        # the exact origin paths.
        origin_watch = {"paths": set()}

        def _refreshable_origins():
            try:
                # Explicit close, not `with`: this connection comes from the
                # pool and must be handed back on every path.
                conn = rt.read_conn()
                try:
                    from flightdeck.treasures import service as _svc
                    return {r["origin_path"] for r in _svc.list_rows(conn, limit=100000)
                            if _svc._refreshable(r)[0]}
                finally:
                    conn.close()
            except Exception:
                return set()

        class OriginHandler(_Handler):
            def on_any_event(self, event):
                if str(getattr(event, "src_path", "")) in origin_watch["paths"] \
                   or str(getattr(event, "dest_path", "")) in origin_watch["paths"]:
                    _schedule_treasures_ping()

        origin_handler = OriginHandler()
        origin_watches: list = []

        def _rebuild_origin_watch():
            """Re-aim the watch at the current set of refreshable origins.

            Called on every debounced treasures ping, so an artifact wrapped from
            a new file starts being watched without a restart.
            """
            paths = _refreshable_origins()
            if paths == origin_watch["paths"]:
                return
            origin_watch["paths"] = paths
            for w in origin_watches:
                try:
                    treasures_observer.unschedule(w)
                except Exception:
                    pass
            origin_watches.clear()
            for parent in {str(Path(p).parent) for p in paths}:
                try:
                    origin_watches.append(treasures_observer.schedule(
                        origin_handler, parent, recursive=False))
                except Exception as e:
                    print(f"[treasures] origin watch skipped {parent}: {e}")

        class TreasuresHandler(_Handler):
            def on_any_event(self, event):
                _schedule_treasures_ping()

        treasures_observer = _Observer()
        treasures_observer.schedule(TreasuresHandler(), str(froot), recursive=True)
        treasures_observer.daemon = True
        treasures_observer.start()
        _rebuild_origin_watch()
        rt.rebuild_origin_watch = _rebuild_origin_watch
    except Exception as e:
        treasures_observer = None
        print(f"[treasures] filestore watch failed to start: {e}")
    app.state.treasures_observer = treasures_observer

    # In-container /usage poll loop. Enabled when TOKEN_AUDIT_POLL_INTERVAL>0
    # AND the claude CLI is reachable (creds mounted). Polls once at startup
    # then every N seconds, writing the RW local report. If claude is missing
    # or unauthenticated each tick is a no-op and quota.read() falls back to
    # the host-cron-written files. Daemon thread; stopped via poll_stop.
    poll_stop = threading.Event()
    app.state.poll_stop = poll_stop
    try:
        interval = int(os.environ.get("TOKEN_AUDIT_POLL_INTERVAL", "0") or 0)
    except ValueError:
        interval = 0
    if interval > 0:
        def _poll_loop():
            while True:
                rt.poll_once(notify=True)
                if poll_stop.wait(interval):
                    return
        threading.Thread(target=_poll_loop, daemon=True).start()

    # Periodic reingest safety net. The filesystem watcher can silently stop
    # delivering events (inotify not propagating across the bind mount for a
    # long-lived append, atomic-replace writes, a debounce/reload race), which
    # freezes the cached snapshot even though the ledger keeps growing. Time-
    # relative ranges ("today"/"7d"/"30d") also go stale at the UTC-midnight
    # rollover with no new data at all. A low-frequency unconditional reingest
    # makes the snapshot converge back to the ledger regardless of the cause.
    try:
        reingest_interval = int(os.environ.get("TOKEN_AUDIT_REINGEST_INTERVAL", "120") or 0)
    except ValueError:
        reingest_interval = 120
    if reingest_interval > 0:
        def _reingest_loop():
            while not poll_stop.wait(reingest_interval):
                try:
                    rt.reingest()
                    loop.call_soon_threadsafe(_updated.set)
                except Exception:
                    traceback.print_exc()
        threading.Thread(target=_reingest_loop, daemon=True).start()

    try:
        yield
    finally:
        poll_stop.set()
        with rt.debounce_lock:
            existing = rt.debounce_timer["timer"]
            if existing is not None:
                existing.cancel()
        with rt.treasures_debounce_lock:
            existing = rt.treasures_debounce_timer["timer"]
            if existing is not None:
                existing.cancel()
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)
        if treasures_observer is not None:
            treasures_observer.stop()
            treasures_observer.join(timeout=2)
