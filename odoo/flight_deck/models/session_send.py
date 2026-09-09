"""Send a message into a Claude Code session from inside Odoo.

Sending needs the `claude` binary, which is on the host and not in this image,
so this calls the FlightDeck backend's own send endpoint and lets it spawn the
process. The container reaches the host through `host.docker.internal`, which
`docker/odoo19/compose.yml` maps with `host-gateway`.

Nothing here writes the reply. The backend appends the turn to the session's
JSONL, and a session with `following` on already tails that file, so the reply
arrives as `flightdeck.message.text` rows through the usual bus notification.
Turning following on before sending is therefore what makes the answer appear.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

BACKEND_URL_PARAM = "flight_deck.backend_url"
DEFAULT_BACKEND_URL = "http://host.docker.internal:8010"
# The backend spawns a model turn, which routinely runs longer than a web
# request would wait for anything else.
SEND_TIMEOUT = 300


class FlightdeckSessionSend(models.Model):
    _inherit = "flightdeck.session"

    def _backend_url(self):
        return (self.env["ir.config_parameter"].sudo()
                .get_param(BACKEND_URL_PARAM, DEFAULT_BACKEND_URL).rstrip("/"))

    def send_message(self, text):
        """Append one user turn and return the reply text.

        Raises UserError for anything the backend refuses, so the reason it
        gives (a live interactive session, a liveness check that could not run)
        reaches the person instead of a traceback.
        """
        self.ensure_one()
        text = (text or "").strip()
        if not text:
            raise UserError(_("Type a message first."))
        if not self.session_id:
            raise UserError(_("This session has no session id to send to."))

        # Following is what brings the reply back, and it must go on BEFORE the
        # send: turning it on records the current end of the file, so a turn
        # written first would sit behind that offset and never arrive.
        if not self.following:
            self.following = True

        url = "%s/api/session/%s/send?%s" % (
            self._backend_url(), urllib.parse.quote(self.session_id),
            urllib.parse.urlencode({"message": text}))
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=SEND_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise UserError(_("FlightDeck refused the message: %s") %
                            self._error_detail(e)) from e
        except urllib.error.URLError as e:
            raise UserError(
                _("Cannot reach FlightDeck at %(url)s (%(reason)s). It must be "
                  "listening on an address this container can route to.",
                  url=self._backend_url(), reason=e.reason)) from e

        _logger.info("sent a message to session %s in %sms",
                     self.session_id, payload.get("duration_ms"))
        return payload.get("reply") or ""

    def _error_detail(self, err):
        """The backend's own `detail`, which says what to do about it."""
        try:
            detail = json.loads(err.read().decode("utf-8")).get("detail")
        except Exception:
            return "HTTP %s" % err.code
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail)
        return detail or "HTTP %s" % err.code
