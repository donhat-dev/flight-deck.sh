"""Run a session through a runner: start, send, interrupt, stop.

Nothing here writes a reply. The runner feeds the text to the claude process,
claude appends the turn to the session's own JSONL, and a session with
`following` on already tails that file, so the reply arrives as
`flightdeck.message.text` rows over the bus. Following therefore goes on
BEFORE the first command, so the offset is recorded before the first byte lands.
"""
import glob
import json
import os
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .runner import EFFORTS, MODELS, MODEL_NOTES, PERMISSION_MODES

RUNTIME_STATES = [
    ("stopped", "Stopped"),
    ("starting", "Starting"),
    ("idle", "Idle"),
    ("busy", "Working"),
]
# A send that the runner only sees this long after it was written is stale.
SEND_TTL = timedelta(minutes=5)
BUS_STATE = "flightdeck.session/state"


class FlightdeckSessionRuntime(models.Model):
    _inherit = "flightdeck.session"

    runner_id = fields.Many2one("flightdeck.runner", ondelete="set null", index=True)
    runtime_state = fields.Selection(RUNTIME_STATES, default="stopped", readonly=True)
    runner_pid = fields.Integer(readonly=True)
    runtime_error = fields.Char(readonly=True)
    cwd = fields.Char("Working directory", help="Where the runner starts claude for this session.")
    runner_cost = fields.Float("Cost via runner", digits=(12, 4), readonly=True,
                               help="Summed from the runner's result frames.")
    live_permission_mode = fields.Selection(
        PERMISSION_MODES, string="Permission mode",
        help="The mode the running process is in. Changing it takes effect on the next turn.")
    live_model = fields.Selection(
        MODELS, string="Model",
        help="The model the running process answers with. Changing it takes effect on the next turn.")
    live_effort = fields.Selection(
        EFFORTS, string="Effort",
        help="How hard the model works. Changing it on a running session costs one turn, because "
             "the CLI takes it as a command rather than over the control channel.")
    decision_ids = fields.One2many("flightdeck.decision", "session_ref_id")
    pending_decision_count = fields.Integer(compute="_compute_pending_decisions")
    allow_live = fields.Boolean(
        "Send even if open elsewhere",
        help="Resume this session even while a terminal holds it. Two writers lose turns.")
    command_ids = fields.One2many("flightdeck.runner.command", "session_ref_id")

    @api.depends("decision_ids.state")
    def _compute_pending_decisions(self):
        for session in self:
            session.pending_decision_count = len(
                session.decision_ids.filtered(lambda d: d.state == "pending"))

    def _transcript_path(self):
        path = super()._transcript_path()
        root = self.runner_id.transcript_root
        if path or not root:
            return path
        found = glob.glob(os.path.join(root, "*", "%s.jsonl" % self.session_id))
        return found[0] if found else False

    def _transcript_cwd(self):
        """The directory the session ran in, from its own first record."""
        path = self._transcript_path()
        if not path:
            return False
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if cwd:
                        return cwd
        except OSError:
            return False
        return False

    def _start_args(self):
        runner = self.runner_id
        tools = [t.strip() for t in (runner.allowed_tools or "").split(",") if t.strip()]
        path = self._transcript_path()
        if not self.cwd:
            # A session that already has a file keeps the directory it ran in,
            # so the same CLAUDE.md and project context load on resume.
            self.cwd = (path and self._transcript_cwd()) or runner.default_cwd or False
        if not self.live_permission_mode:
            self.live_permission_mode = runner.permission_mode
        if not self.live_model:
            self.live_model = runner.default_model or "default"
        if not self.live_effort and runner.default_effort:
            self.live_effort = runner.default_effort
        return {
            "cwd": self.cwd or "",
            "resume": bool(path),
            "allow_live": self.allow_live,
            "permission_mode": self.live_permission_mode,
            # "default" is this side's word for "say nothing and let the account
            # decide"; the CLI has no such name.
            "model": "" if self.live_model == "default" else (self.live_model or ""),
            "effort": self.live_effort or "",
            "allowed_tools": tools,
        }

    def action_start_runtime(self):
        for session in self:
            if not session.runner_id:
                raise UserError(_("Pick a runner for this session first."))
            if not session.following:
                session.write({"following": True})
            session.runner_id._submit("start", session, session._start_args())
            session._set_runtime_state("starting")
        return True

    def action_stop_runtime(self):
        for session in self.filtered("runner_id"):
            session.runner_id._submit("stop", session)
        return True

    def action_interrupt_runtime(self):
        for session in self.filtered("runner_id"):
            session.runner_id._submit("interrupt", session)
        return True

    def action_open_decisions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.decision",
            "name": "Approvals — %s" % self.name,
            "view_mode": "list,form",
            "domain": [("session_ref_id", "=", self.id)],
        }

    def write(self, vals):
        """Picking a permission mode is one act, wherever it is picked.

        The chat box and the field on the form both write this record, so the
        command that tells the running process rides the write itself. The
        runner's own echo of a mode it already applied carries `fd_no_mode_cmd`
        so it does not bounce back as a new command.
        """
        res = super().write(vals)
        picks = {
            "live_permission_mode": ("set_mode", "mode"),
            "live_model": ("set_model", "model"),
            "live_effort": ("set_effort", "effort"),
        }
        changed = {f: vals[f] for f in picks if vals.get(f)}
        if not changed:
            return res
        echo = self.env.context.get("fd_no_mode_cmd")
        for session in self:
            payload = {
                "session_id": session.id,
                "runtime_state": session.runtime_state,
                "error": "",
            }
            for field, value in changed.items():
                op, key = picks[field]
                if not echo and session.runtime_state != "stopped" and session.runner_id:
                    arg = "" if (field == "live_model" and value == "default") else value
                    session.runner_id._submit(op, session, {key: arg})
                payload[key] = value
            # The chat box carries these too, and it is not part of the form's
            # record, so it hears about the change this way.
            session._bus_send(BUS_STATE, payload)
        return res

    def action_set_mode(self, mode):
        """Switch the permission mode from the chat box."""
        self.ensure_one()
        if mode not in dict(PERMISSION_MODES):
            raise UserError(_("Unknown permission mode %s.") % mode)
        self.live_permission_mode = mode
        return True

    def action_set_model(self, model):
        self.ensure_one()
        if model not in dict(MODELS):
            raise UserError(_("Unknown model %s.") % model)
        self.live_model = model
        return True

    def action_set_effort(self, effort):
        self.ensure_one()
        if effort not in dict(EFFORTS):
            raise UserError(_("Unknown effort level %s.") % effort)
        self.live_effort = effort
        return True

    @api.model
    def fd_model_choices(self):
        """What the chat box offers when picking a model or an effort level."""
        return {
            "models": [{"value": v, "label": label, "note": MODEL_NOTES.get(v, "")}
                       for v, label in MODELS],
            "efforts": [{"value": v, "label": label} for v, label in EFFORTS],
        }

    def send_message(self, text):
        """Queue one user turn. Returns as soon as the command is written."""
        self.ensure_one()
        text = (text or "").strip()
        if not text:
            raise UserError(_("Type a message first."))
        if not self.runner_id:
            raise UserError(_("This session has no runner. Pick one on the form."))
        if self.runtime_state == "stopped":
            self.action_start_runtime()
        self.runner_id._submit("send", self, {"text": text}, expires_in=SEND_TTL)
        self._set_runtime_state("busy")
        return True

    def _set_runtime_state(self, state, pid=None, error=None):
        vals = {"runtime_state": state, "runtime_error": error or False}
        if pid is not None:
            vals["runner_pid"] = pid
        if state == "stopped":
            vals["runner_pid"] = 0
        for session in self:
            session.write(vals)
            session._bus_send(BUS_STATE, {
                "session_id": session.id,
                "runtime_state": state,
                "error": error or "",
            })

    @api.model
    def _cron_follow(self):
        self.env["flightdeck.runner"]._cron_poll_events()
        return super()._cron_follow()
