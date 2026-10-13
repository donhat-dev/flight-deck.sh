"""Read every transcript again once, for what the task rollup needs.

Nothing is read here; the crons do it on their next runs. Resetting the busy
time cursors makes the reader store each turn's cwd, branch, prompt text and
byte range, and tie each agent file to a person's turn. Resetting the spend
cursors makes the spend pass write the prompt and agent onto rows it already
holds, and read the agents' files, whose calls were not in the ledger.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("UPDATE flightdeck_activity_cursor SET bytes_read = 0, spend_bytes = 0, state = NULL")
    files = cr.rowcount
    cr.execute("UPDATE flightdeck_session SET spend_bytes = 0 WHERE spend_bytes > 0")
    _logger.info("flight_deck: %s transcript file(s) and %s session(s) set to be read again",
                 files, cr.rowcount)
