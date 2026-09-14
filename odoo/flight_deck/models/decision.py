"""One permission request, parked until a person answers it.

The claude process asks its host before running a tool the permission mode did
not settle. The runner is that host: it parks the turn and writes a
`permission` event, which becomes one of these records. Answering writes a
`decide` command back, and the turn resumes.

Nothing here can approve on its own. A record nobody answers is denied by the
runner's own timeout, which also closes the record.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

BUS_ASK = "flightdeck.session/permission"
BUS_DECIDED = "flightdeck.session/decision"

SCOPES = [
    ("once", "This time"),
    ("session", "For this session"),
    ("always", "Always in this project"),
]


class FlightdeckDecision(models.Model):
    _name = "flightdeck.decision"
    _description = "Permission request waiting on a person"
    _order = "create_date desc"
    _rec_name = "tool_name"

    session_ref_id = fields.Many2one(
        "flightdeck.session", required=True, ondelete="cascade", index=True)
    runner_id = fields.Many2one(related="session_ref_id.runner_id", store=True)
    request_id = fields.Char(required=True, index=True)
    tool_use_id = fields.Char()
    tool_name = fields.Char(required=True)
    # The tool's own arguments, as the model asked for them.
    tool_input = fields.Text()
    summary = fields.Char(help="The one line worth reading before deciding.")
    description = fields.Text()
    can_remember = fields.Boolean(
        help="The CLI offered a rule for this call, so it can be remembered.")
    blocked_path = fields.Char(
        "Path outside the session",
        help="What the call reaches outside its working directory. This is a gate of its "
             "own: allowing the command without allowing the path asks again next time.")

    state = fields.Selection([
        ("pending", "Waiting"),
        ("allowed", "Allowed"),
        ("denied", "Denied"),
        ("expired", "Expired"),
    ], default="pending", required=True, index=True)
    scope = fields.Selection(SCOPES, default="once")
    message = fields.Char("Reason")
    decided_by = fields.Many2one("res.users", readonly=True)
    decided_at = fields.Datetime(readonly=True)
    waited_ms = fields.Integer(readonly=True)

    _request_uniq = models.Constraint(
        "unique(request_id)", "This permission request is already recorded.")

    # --- from the runner -----------------------------------------------------
    @api.model
    def _from_event(self, session, payload):
        """Record a request the runner parked, and tell the open form."""
        existing = self.search([("request_id", "=", payload.get("request_id"))], limit=1)
        if existing:
            return existing
        raw = payload.get("input")
        record = self.create({
            "session_ref_id": session.id,
            "request_id": payload.get("request_id"),
            "tool_use_id": payload.get("tool_use_id") or False,
            "tool_name": payload.get("tool_name") or "tool",
            "tool_input": json.dumps(raw, indent=2, ensure_ascii=False) if raw else False,
            "summary": payload.get("title") or self._one_line(raw),
            "description": payload.get("description") or False,
            "can_remember": bool(payload.get("can_remember")),
            "blocked_path": payload.get("blocked_path") or False,
        })
        session._bus_send(BUS_ASK, record._bus_payload())
        return record

    def _resolve(self, payload):
        """Close a record the runner answered, whoever caused it."""
        self.ensure_one()
        behavior = payload.get("behavior")
        vals = {"waited_ms": payload.get("waited_ms") or 0}
        if self.state == "pending":
            vals["state"] = "allowed" if behavior == "allow" else "denied"
            if payload.get("reason"):
                vals["message"] = payload["reason"]
        self.write(vals)
        self.session_ref_id._bus_send(BUS_DECIDED, {
            "session_id": self.session_ref_id.id,
            "id": self.id,
            "request_id": self.request_id,
            "state": self.state,
        })

    def _one_line(self, raw):
        """What a person needs to read first: the command, the path, the URL."""
        if not isinstance(raw, dict):
            return False
        for key in ("command", "file_path", "url", "query", "pattern", "path"):
            if raw.get(key):
                return str(raw[key])[:250]
        return False

    def _bus_payload(self):
        self.ensure_one()
        return {
            "session_id": self.session_ref_id.id,
            "id": self.id,
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input or "",
            "summary": self.summary or "",
            "description": self.description or "",
            "can_remember": self.can_remember,
            "blocked_path": self.blocked_path or "",
        }

    # --- from a person -------------------------------------------------------
    def decide(self, behavior, scope="once", message=None):
        """Answer this request. Called from the form and from the chat box."""
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("This request was already answered (%s).") % self.state)
        session = self.session_ref_id
        if not session.runner_id:
            raise UserError(_("The session has no runner to answer through."))
        if behavior not in ("allow", "deny"):
            raise UserError(_("A decision is allow or deny."))
        if scope not in dict(SCOPES):
            scope = "once"
        session.runner_id._submit("decide", session, {
            "request_id": self.request_id,
            "behavior": behavior,
            "scope": scope if behavior == "allow" else "once",
            "message": message or "",
        })
        # The runner's own `decision` event closes the record; this is what the
        # person who clicked sees in the meantime.
        self.write({
            "state": "allowed" if behavior == "allow" else "denied",
            "scope": scope,
            "message": message or self.message,
            "decided_by": self.env.user.id,
            "decided_at": fields.Datetime.now(),
        })
        session._bus_send(BUS_DECIDED, {
            "session_id": session.id,
            "id": self.id,
            "request_id": self.request_id,
            "state": self.state,
        })
        return True

    def action_allow(self):
        for record in self:
            record.decide("allow", "once")
        return True

    def action_allow_always(self):
        for record in self:
            record.decide("allow", "always")
        return True

    def action_deny(self):
        for record in self:
            record.decide("deny")
        return True
