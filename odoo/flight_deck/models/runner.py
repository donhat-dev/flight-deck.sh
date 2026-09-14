"""A runner: the process that keeps claude sessions alive for Odoo.

Odoo never spawns claude itself. It writes commands through a connector and
reads the runner's events back on the follow cron. Neither step is deferred
work: a send is one file write in the request, and the event read rides the
cron that already tails transcripts, so no job queue is involved.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..connectors import get_connector, kinds

_logger = logging.getLogger(__name__)

# What the terminal cycles through with shift+tab, in the same order. `dontAsk`
# never asks, so a session left there can only run what a rule already allows.
PERMISSION_MODES = [
    ("default", "Ask me"),
    ("acceptEdits", "Accept edits"),
    ("plan", "Plan only"),
    ("auto", "Auto"),
    ("dontAsk", "Never ask"),
]

# The names the CLI takes for --model. "default" means: pass nothing and let
# the signed-in account decide.
MODELS = [
    ("default", "Default"),
    ("opus", "Opus 5"),
    ("fable", "Fable 5.1"),
    ("sonnet", "Sonnet 5"),
    ("haiku", "Haiku 4.5"),
]
# What each one is for, one line, shown next to the name when picking.
MODEL_NOTES = {
    "default": "Whatever the signed-in account uses.",
    "opus": "1M context. Everyday and complex work.",
    "fable": "The most capable, for the hardest and longest runs.",
    "sonnet": "Efficient for routine work.",
    "haiku": "Fastest, for quick answers.",
}
EFFORTS = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("xhigh", "Extra high"),
    ("max", "Max"),
]


class FlightdeckRunner(models.Model):
    _name = "flightdeck.runner"
    _description = "FlightDeck runner"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        selection=lambda self: [(k, k) for k in kinds()], default="spool", required=True,
        help="How commands reach the runner.")
    zone = fields.Char(help="Where the runner's processes live: host or container.")
    spool_dir = fields.Char(
        "Spool directory",
        help="The runner's zone directory, as this Odoo sees it.")
    transcript_root = fields.Char(
        help="Where this runner's claude writes session files, as this Odoo sees it. "
             "Left empty, the default transcript root is searched.")
    default_cwd = fields.Char(
        "Working directory",
        help="Where claude starts for a new session, as the runner sees it.")
    default_model = fields.Selection(
        MODELS, string="Model", default="default", required=True,
        help="Where a new session starts.")
    default_effort = fields.Selection(
        EFFORTS, string="Effort",
        help="How hard the model works on a turn. Empty is the CLI default.")
    permission_mode = fields.Selection(
        PERMISSION_MODES, default="default", required=True,
        string="Permission mode", help="Where a new session starts.")
    allowed_tools = fields.Char(help="Comma separated permission rules, e.g. Read,Bash(git diff *).")
    events_offset = fields.Integer(readonly=True)
    next_seq = fields.Integer(default=1, readonly=True)
    last_event_at = fields.Datetime(readonly=True)
    status = fields.Char(compute="_compute_status")
    command_ids = fields.One2many("flightdeck.runner.command", "runner_id")
    session_ids = fields.One2many("flightdeck.session", "runner_id")

    def _connector(self):
        self.ensure_one()
        return get_connector(self)

    @api.depends("spool_dir", "kind")
    def _compute_status(self):
        for runner in self:
            try:
                runner.status = runner._connector().status()
            except Exception as e:  # noqa: BLE001 - shown to the person instead
                runner.status = str(e)

    def _submit(self, op, session, args=None, expires_in=None):
        """Write one command and its audit row."""
        self.ensure_one()
        seq = self.next_seq
        self.next_seq = seq + 1
        command = {
            "cmd_id": str(uuid.uuid4()),
            "seq": seq,
            "op": op,
            "session_id": session.session_id,
            "args": args or {},
        }
        if expires_in:
            due = datetime.now(timezone.utc) + expires_in
            command["expires_at"] = due.strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self.env["flightdeck.runner.command"].create({
            "runner_id": self.id,
            "session_ref_id": session.id,
            "cmd_id": command["cmd_id"],
            "seq": seq,
            "op": op,
            "args": json.dumps(command["args"]),
        })
        try:
            self._connector().submit(command)
        except OSError as e:
            row.write({"state": "failed", "error": str(e)})
            raise UserError("The runner's spool is not writable: %s" % e) from e
        return row

    # --- events --------------------------------------------------------------
    @api.model
    def _cron_poll_events(self):
        for runner in self.search([]):
            try:
                runner._poll_events()
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception("reading events failed for runner %s", runner.id)
        return True

    def _poll_events(self):
        self.ensure_one()
        connector = self._connector()
        events, offset = connector.read_events(self.events_offset)
        for event in events:
            self._apply_event(event)
        if offset != self.events_offset:
            self.write({"events_offset": offset, "last_event_at": fields.Datetime.now()})
        self._reconcile(connector)
        return len(events)

    def _reconcile(self, connector):
        """A runner that died took its processes with it and wrote no event
        for them. Whatever it does not list as live is stopped."""
        live = getattr(connector, "live_states", None)
        if live is None:
            return
        running = set(live())
        for session in self.session_ids.filtered(lambda s: s.runtime_state != "stopped"):
            # A start the runner has not picked up yet has no state file either.
            if any(c.state == "queued" for c in session.command_ids):
                continue
            if session.session_id not in running:
                session._set_runtime_state("stopped", error="The runner no longer has this process.")

    def _apply_event(self, event):
        kind = event.get("kind")
        if kind == "ack" and event.get("cmd_id"):
            row = self.env["flightdeck.runner.command"].search(
                [("cmd_id", "=", event["cmd_id"])], limit=1)
            if row:
                row.write({
                    "state": "failed" if event.get("error") else "done",
                    "error": event.get("error") or False,
                })
                session = row.session_ref_id
                if event.get("error") and session:
                    if row.op == "start":
                        session._set_runtime_state("stopped", error=event["error"])
                    elif row.op in ("send", "interrupt") and session.runtime_state != "stopped":
                        # A refused start already said why; a later send failing
                        # on the same session must not overwrite that reason.
                        session._set_runtime_state("stopped", error=event["error"])
            return
        session = False
        if event.get("session_id"):
            session = self.env["flightdeck.session"].search(
                [("session_id", "=", event["session_id"])], limit=1)
        if not session:
            return
        if kind == "state":
            pid = (event.get("payload") or {}).get("pid")
            session._set_runtime_state(event.get("state"), pid=pid, error=event.get("error"))
        elif kind == "permission":
            self.env["flightdeck.decision"]._from_event(session, event.get("payload") or {})
        elif kind == "decision":
            payload = event.get("payload") or {}
            record = self.env["flightdeck.decision"].search(
                [("request_id", "=", payload.get("request_id"))], limit=1)
            if record:
                record._resolve(payload)
        elif kind in ("mode", "model", "effort"):
            payload = event.get("payload") or {}
            field = {"mode": "live_permission_mode", "model": "live_model",
                     "effort": "live_effort"}[kind]
            value = payload.get(kind) or ("default" if kind == "model" else False)
            if value:
                session.with_context(fd_no_mode_cmd=True).write({field: value})
        elif kind == "result":
            payload = event.get("payload") or {}
            session.write({
                "runner_cost": session.runner_cost + (payload.get("total_cost_usd") or 0.0),
                "end_ts": fields.Datetime.now(),
            })

    # --- actions -------------------------------------------------------------
    def action_new_session(self):
        """Create a session on this runner and open it."""
        self.ensure_one()
        session = self.env["flightdeck.session"].create({
            "session_id": str(uuid.uuid4()),
            "name": "New session %s" % fields.Datetime.now().strftime("%Y-%m-%d %H:%M"),
            "title_source": "custom",
            "runner_id": self.id,
            "cwd": self.default_cwd or False,
            "start_ts": fields.Datetime.now(),
        })
        # A write, not a create flag: write() is what arms the follow offset.
        session.write({"following": True})
        session.action_start_runtime()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.session",
            "res_id": session.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_poll_now(self):
        for runner in self:
            runner._poll_events()
        return True


class FlightdeckRunnerCommand(models.Model):
    _name = "flightdeck.runner.command"
    _description = "Command sent to a runner"
    _order = "seq desc"
    _rec_name = "cmd_id"

    runner_id = fields.Many2one("flightdeck.runner", required=True, ondelete="cascade", index=True)
    session_ref_id = fields.Many2one("flightdeck.session", ondelete="set null", index=True)
    cmd_id = fields.Char(required=True, index=True)
    seq = fields.Integer(required=True)
    op = fields.Selection([
        ("start", "Start"), ("send", "Send"), ("interrupt", "Interrupt"), ("stop", "Stop"),
        ("decide", "Answer a permission request"), ("set_mode", "Change permission mode"),
    ], required=True)
    state = fields.Selection([
        ("queued", "Queued"), ("done", "Done"), ("failed", "Failed"),
    ], default="queued", required=True)
    args = fields.Text()
    error = fields.Text()
