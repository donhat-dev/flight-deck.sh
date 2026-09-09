import json

from odoo import http
from odoo.http import request
from odoo.exceptions import UserError


class FlightDeckSend(http.Controller):
    """Send a message into a session, addressed the way the backend is.

    The session is addressed by its Claude session id rather than the Odoo row
    id, so the same URL works against this controller and against the backend
    endpoint it calls.
    """

    @http.route("/flight_deck/session/<string:session_id>/send",
                type="http", auth="user", methods=["POST"], csrf=False)
    def send(self, session_id, message=None, **kw):
        session = request.env["flightdeck.session"].search(
            [("session_id", "=", session_id)], limit=1)
        if not session:
            return self._json({"error": "session not found"}, 404)
        try:
            reply = session.send_message(message)
        except UserError as e:
            return self._json({"error": str(e)}, 502)
        return self._json({"session_id": session_id, "reply": reply})

    def _json(self, payload, status=200):
        return request.make_response(
            json.dumps(payload),
            headers=[("Content-Type", "application/json")],
            status=status,
        )


class FlightDeckArtifact(http.Controller):
    """Serve a treasure's rendered page as a document rather than a download.

    `/web/content` answers with a Content-Disposition that makes a browser save
    the file; the preview needs it displayed inside an iframe instead.
    """

    @http.route("/flight_deck/artifact/<int:treasure_id>", type="http", auth="user")
    def artifact(self, treasure_id, **kw):
        treasure = request.env["flightdeck.treasure"].browse(treasure_id).exists()
        if not treasure or not treasure.attachment_id:
            return request.not_found()
        return request.make_response(
            treasure.attachment_id.raw,
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )
