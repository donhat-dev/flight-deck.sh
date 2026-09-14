"""Watch the transcript files of followed sessions and wake the cron.

The watcher is deliberately thin: it reads nothing and writes nothing. Its only
act is `ir.cron._trigger()`, which inserts a trigger row and sends
`pg_notify('cron_trigger')` — the cron thread waits on that notification, so the
read happens within milliseconds instead of at the next 60 second wake-up.

Keeping the work in the cron is what makes this safe. The cron is Odoo's own
single-runner, with its row lock, its own cursor and its own transaction, so a
watcher that dies costs latency and nothing else.

Only one process may watch a database, hence the advisory lock: with several
workers every one of them would fork its own watcher.
"""
import logging
import os
import threading
import time

import psycopg2

import odoo
from odoo import api, SUPERUSER_ID
from odoo.orm.registry import Registry
from odoo.tools import config

_logger = logging.getLogger(__name__)

# Any stable number. It names this watcher among Postgres advisory locks.
LOCK_KEY = 0x1F1D0001
REFRESH_SECONDS = 5.0
DEBOUNCE_SECONDS = 0.3
CRON_XMLID = "flight_deck.cron_follow"

_watchers = {}
_guard = threading.Lock()


def ensure_started(dbname):
    """Start the watcher for a database, once per process."""
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
        """Directories holding a followed session's file.

        Directories rather than files: an editor or a rotation replaces a file
        and an inode watch would follow the old one.
        """
        dirs = set()
        try:
            with Registry(self.dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                for session in env["flightdeck.session"].search([("following", "=", True)]):
                    path = session._transcript_path()
                    if path:
                        dirs.add(os.path.dirname(path))
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

    # --- the one act --------------------------------------------------------
    def _wake_cron(self):
        try:
            with Registry(self.dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env.ref(CRON_XMLID).sudo()._trigger()
                cr.commit()
        except Exception:
            _logger.exception("could not trigger the follow cron")

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
                if str(event.src_path).endswith(".jsonl"):
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
                time.sleep(0.1)
        except Exception:
            _logger.exception("transcript watcher stopped")
        finally:
            try:
                self._observer.stop()
            except Exception:
                pass
