"""Watch the transcript directories and wake the crons that read them.

The watcher is deliberately thin. Its rule used to be "reads nothing, writes
nothing"; it is now "publishes nothing that writes a business row." Its main
act is still `ir.cron._trigger()`, which inserts a trigger row and sends
`pg_notify('cron_trigger')` — the cron thread waits on that notification, so
the read happens within milliseconds instead of at the next 60 second
wake-up. Everything that creates or updates a `flightdeck.*` record — a
transcript line, a session's runtime state, a decision — still goes through
that cron, on its own cursor, never here.

The one exception is streaming text: the watcher reads `deltas.jsonl` itself
and puts each snapshot straight on the bus (see `_publish_deltas`). That is
safe precisely because a delta write is not a business row — nothing is lost
if the watcher dies mid-read, the next debounce reads on from its own
in-memory cursor, and the worst a bug here costs is a draft that lags or
never appears. Routing it through `ir.cron._trigger()` instead would buy
nothing: the cron's transaction and row lock exist to make a durable write
safe against a concurrent one, and a bus frame is neither.

Keeping the durable work in the cron is what makes the rest of this safe. The
cron is Odoo's own single-runner, with its row lock, its own cursor and its
own transaction, so a watcher that dies costs latency and nothing else.

Only one process may watch a database, hence the advisory lock: with several
workers every one of them would fork its own watcher.
"""
import glob
import logging
import os
import threading
import time

import psycopg2

import odoo
from odoo import api, SUPERUSER_ID
from odoo.orm.registry import Registry
from odoo.tools import config

from ..connectors import get_connector

_logger = logging.getLogger(__name__)

# Any stable number. It names this watcher among Postgres advisory locks.
LOCK_KEY = 0x1F1D0001
REFRESH_SECONDS = 5.0
DEBOUNCE_SECONDS = 0.3
# The scan is deliberately absent. It reads every transcript and queues the
# work, which is the one thing that must not happen on every file event.
CRON_XMLIDS = ("flight_deck.cron_follow",)
# What the browser draws as the unfinished line at the head of the transcript.
BUS_DELTA = "flightdeck.transcript/delta"

_watchers = {}
_guard = threading.Lock()


def ensure_started(dbname):
    """Start the watcher for a database, once per process."""
    # An upgrade or test run (--stop-after-init) ends right after loading; a
    # watcher started there only queries a registry that is shutting down.
    if config.get("stop_after_init"):
        return None
    with _guard:
        watcher = _watchers.get(dbname)
        if watcher and watcher.is_alive():
            return watcher
        try:
            watcher = _Watcher(dbname)
            watcher.start()
        except Exception:
            _logger.exception("could not start the transcript watcher")
            return None
        _watchers[dbname] = watcher
        return watcher


class _Watcher(threading.Thread):
    def __init__(self, dbname):
        super().__init__(name="fd-follow-watcher", daemon=True)
        self.dbname = dbname
        self._lock_conn = None
        self._observer = None
        self._watched = {}
        self._due = 0.0
        # A separate debounce clock from `_due`: see `run`'s Handler for why
        # deltas.jsonl must never set `_due` itself.
        self._delta_due = 0.0
        # Byte offset into each runner's deltas.jsonl, kept on this thread
        # only. The file itself carries nothing durable (the runner truncates
        # it on its own once it grows past its cap), so a cursor that
        # outlived this process would just be a stale number for a restart to
        # discard — never a field.
        self._delta_offsets = {}

    # --- single runner ------------------------------------------------------
    def _take_lock(self):
        """A connection of its own, outside Odoo's pool.

        The lock lives for as long as the session holding it, and a pooled
        cursor is returned to the pool as soon as it is done — which would drop
        the lock on the next statement someone else runs on it.
        """
        params = {
            "dbname": self.dbname,
            "host": config["db_host"] or None,
            "port": config["db_port"] or None,
            "user": config["db_user"] or None,
            "password": config["db_password"] or None,
        }
        self._lock_conn = psycopg2.connect(**{k: v for k, v in params.items() if v})
        self._lock_conn.autocommit = True
        cr = self._lock_conn.cursor()
        cr.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        taken = cr.fetchone()[0]
        if not taken:
            self._lock_conn.close()
            self._lock_conn = None
        return taken

    # --- what to watch ------------------------------------------------------
    def _followed_dirs(self):
        """Directories holding a transcript this database cares about.

        Every project directory, not only the followed sessions': a session
        that does not exist yet is exactly the thing the scan has to notice,
        and its file appears in one of these.

        Directories rather than files: an editor or a rotation replaces a file
        and an inode watch would follow the old one.
        """
        from .session import TRANSCRIPT_ROOT

        dirs = {path for path in glob.glob(os.path.join(TRANSCRIPT_ROOT, "*"))
                if os.path.isdir(path)}
        try:
            with Registry(self.dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                for session in env["flightdeck.session"].search([("following", "=", True)]):
                    path = session._transcript_path()
                    if path:
                        dirs.add(os.path.dirname(path))
                    # An agent's turns land here and nowhere else until it
                    # answers, so a session watched without this one goes quiet
                    # for as long as its agents run.
                    agents = session._subagent_dir()
                    if agents:
                        dirs.add(agents)
                        flow = os.path.join(agents, "workflows")
                        for found in glob.glob(os.path.join(flow, "*")):
                            if os.path.isdir(found):
                                dirs.add(found)
                # A runner's events file lives in its spool directory, and it
                # is written right after the turn lands in the transcript.
                for runner in env["flightdeck.runner"].search([]):
                    if runner.spool_dir and os.path.isdir(runner.spool_dir):
                        dirs.add(runner.spool_dir)
        except Exception:
            _logger.exception("could not list followed sessions")
        return dirs

    def _resync(self, handler):
        wanted = self._followed_dirs()
        for path in list(self._watched):
            if path not in wanted:
                self._observer.unschedule(self._watched.pop(path))
        for path in wanted - set(self._watched):
            if os.path.isdir(path):
                self._watched[path] = self._observer.schedule(handler, path, recursive=False)

    # --- the one durable act -------------------------------------------------
    def _wake_cron(self):
        try:
            with Registry(self.dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                for xmlid in CRON_XMLIDS:
                    env.ref(xmlid).sudo()._trigger()
                cr.commit()
        except Exception:
            _logger.exception("could not trigger the transcript crons")

    # --- the one non-durable act ---------------------------------------------
    def _publish_deltas(self):
        """Read each runner's new streaming-text lines and bus-send them.

        On its own cursor, committed here rather than left for the cron: a
        `bus.bus` insert is how `_bus_send` always works, cron or not, and
        nothing durable to the product is at stake in it. A failure here must
        cost only smoothness — a draft that lags or skips a beat — so it is
        caught and logged rather than allowed to reach the caller, which
        would stop the debounce loop it runs inside.
        """
        try:
            with Registry(self.dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                sessions_by_sid = None
                for runner in env["flightdeck.runner"].search([]):
                    offset = self._delta_offsets.get(runner.id, 0)
                    try:
                        rows, offset = get_connector(runner).read_deltas(offset)
                    except Exception:
                        _logger.exception("could not read deltas for runner %s", runner.id)
                        continue
                    self._delta_offsets[runner.id] = offset
                    if not rows:
                        continue
                    if sessions_by_sid is None:
                        # Built once per pass, lazily: most passes touch one
                        # or two live sessions out of the whole logbook.
                        sessions_by_sid = {}
                    wanted = {row.get("session_id") for row in rows if row.get("session_id")}
                    missing = wanted - set(sessions_by_sid)
                    if missing:
                        found = env["flightdeck.session"].search(
                            [("session_id", "in", list(missing))])
                        sessions_by_sid.update({s.session_id: s for s in found})
                        # A session id neither found nor worth searching again
                        # this pass: hold the miss too, or a delta line for an
                        # unimported or since-deleted session re-triggers the
                        # same search on every remaining row.
                        for sid in missing - set(sessions_by_sid):
                            sessions_by_sid[sid] = None
                    for row in rows:
                        session = sessions_by_sid.get(row.get("session_id"))
                        if not session:
                            continue
                        session._bus_send(BUS_DELTA, {
                            "session_id": session.id,
                            "stream": row.get("stream"),
                            "seq": row.get("seq"),
                            "text": row.get("text"),
                        })
                cr.commit()
        except Exception:
            _logger.exception("could not publish transcript deltas")

    def run(self):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        if not self._take_lock():
            _logger.info("transcript watcher: another process holds it")
            return

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                # Only new bytes. A read emits events too, and answering those
                # is how a watcher ends up feeding itself.
                if event.event_type not in ("modified", "created", "moved"):
                    return
                path = str(event.src_path)
                # deltas.jsonl gets its own clock, wired to `_publish_deltas`
                # only — sharing `_due` would mean the cron behind every
                # durable read wakes as often as the runner writes a token,
                # exactly the regression streaming must not cause.
                #
                # It is a throttle, not a debounce: set only while nothing is
                # already pending, never pushed forward on every write. A
                # debounce (like `_due` below) waits for quiet before firing,
                # which is right for coalescing a burst of transcript writes
                # ahead of one cron wake-up, but wrong here — deltas.jsonl
                # never goes quiet for 0.3s while a reply is still streaming,
                # so a debounce would only ever fire once the reply is
                # already finished, and the draft would just jump straight to
                # the final text instead of growing.
                if path.endswith("/deltas.jsonl"):
                    if not watcher._delta_due:
                        watcher._delta_due = time.time() + DEBOUNCE_SECONDS
                elif path.endswith(".jsonl"):
                    watcher._due = time.time() + DEBOUNCE_SECONDS

        handler = Handler()
        self._observer = Observer()
        self._observer.start()
        _logger.info("transcript watcher started for %s", self.dbname)

        last_resync = 0.0
        try:
            while True:
                now = time.time()
                if now - last_resync > REFRESH_SECONDS:
                    self._resync(handler)
                    last_resync = now
                if self._due and now >= self._due:
                    self._due = 0.0
                    self._wake_cron()
                if self._delta_due and now >= self._delta_due:
                    self._delta_due = 0.0
                    self._publish_deltas()
                time.sleep(0.1)
        except Exception:
            _logger.exception("transcript watcher stopped")
        finally:
            try:
                self._observer.stop()
            except Exception:
                pass
