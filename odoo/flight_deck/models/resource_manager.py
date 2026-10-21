import json
import logging
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .resource_check import (
    CHAT_MAX_CHARS, PROCESS_IDENTITY_FIELDS, expected_from_row, parse_expiry,
    valid_action_kind, valid_resource_key, validate_expected_process,
)
from .resource_context import build_resource_chat_prompt, resource_snapshot_fresh
from .resource_policy import (
    RESOURCE_ACTION_ROWS, RESOURCE_ACTION_TTL_SECONDS, RESOURCE_ACTIONS,
)
from .resource_prompts import RESOURCE_AGENT_INSTRUCTIONS

_logger = logging.getLogger(__name__)

CHAT_MAX_KEYS = 50
KEY_MAX_CHARS = 1024


class FlightdeckRunnerResources(models.Model):
    _inherit = "flightdeck.runner"

    resource_agent_id = fields.Many2one(
        "flightdeck.session", string="Resource management session",
        readonly=True, copy=False, index=True)

    def _resource_runner(self, runner_id=None):
        Runner = self.env["flightdeck.runner"]
        if runner_id:
            runner = Runner.browse(int(runner_id)).exists()
            if not runner or not runner.active or runner.kind != "spool":
                raise UserError(_("Unknown or inactive runner."))
            runner.check_access("read")
            return runner
        runner = Runner.search(
            [("active", "=", True), ("kind", "=", "spool")], order="name", limit=1)
        if not runner:
            raise UserError(_("No active runner is configured."))
        return runner

    @api.model
    def fd_resource_dashboard(self, runner_id=None):
        Runner = self.env["flightdeck.runner"]
        runners = Runner.search([("active", "=", True), ("kind", "=", "spool")], order="name")
        if not runners:
            return {"runners": [], "runner_id": False, "snapshot": None,
                    "actions": [], "agent": None, "chat": [],
                    "can_manage": self.env.user.has_group("flight_deck.group_manager")}
        runner = self._resource_runner(runner_id) if runner_id else runners[0]
        snapshot = runner._connector().resource_snapshot()
        actions = self.env["flightdeck.resource.action"].search(
            [("runner_id", "=", runner.id)], order="id desc", limit=RESOURCE_ACTION_ROWS)
        session = runner.resource_agent_id
        chat = []
        if session:
            replies = self.env["flightdeck.session.reply"].search(
                [("session_ref_id", "=", session.id)], order="id desc", limit=20)
            replies = replies.filtered(lambda r: r.resource_operator_prompt)
            for reply in reversed(replies):
                answer = reply.reply or reply._answer_text()
                chat.append({
                    "reply_id": reply.id,
                    "prompt": reply.resource_operator_prompt,
                    "state": reply.state,
                    "answer": answer or "",
                })
        return {
            "runners": [{"id": r.id, "name": r.name, "zone": r.zone or ""} for r in runners],
            "runner_id": runner.id,
            "snapshot": snapshot,
            "snapshot_fresh": resource_snapshot_fresh(snapshot),
            "actions": [{
                "id": a.id, "proposal_id": a.proposal_id, "action_kind": a.action_kind,
                "resource_key": a.resource_key, "reason": a.reason or "",
                "state": a.state, "result": a.result or "",
                "expires_at": fields.Datetime.to_string(a.expires_at),
                "session_uuid": a.session_uuid or "", "server_name": a.server_name or "",
                "command_uuid": a.command_uuid or "",
                "requested_by": a.requested_by.name or "" if a.requested_by else "",
                "confirmed_by": a.confirmed_by.name or "" if a.confirmed_by else "",
                "confirmed_at": fields.Datetime.to_string(a.confirmed_at),
            } for a in actions],
            "agent": {
                "session_uuid": session.session_id if session else "",
                "state": session.runtime_state if session else "stopped",
                "error": session.runtime_error or "" if session else "",
                "pid": session.runner_pid if session else 0,
                "busy": session.runtime_state == "busy" if session else False,
                "starting": session.runtime_state == "starting" if session else False,
            },
            "chat": chat,
            "can_manage": self.env.user.has_group("flight_deck.group_manager"),
        }

    @api.model
    def _require_manager(self):
        if not self.env.user.has_group("flight_deck.group_manager"):
            raise AccessError(_("Only a FlightDeck manager can manage resources."))

    @api.model
    def fd_resource_scan(self, runner_id):
        self._require_manager()
        runner = self._resource_runner(runner_id)
        runner._submit("resource_scan")
        return True

    def _resource_agent_session(self, runner):
        chat_key = "resources:%d" % runner.id
        Session = self.env["flightdeck.session"]
        self.env.cr.execute(
            "SELECT id FROM flightdeck_runner WHERE id = %s FOR UPDATE", (runner.id,))
        self.env.invalidate_all()
        runner = runner.browse(runner.id)
        session = runner.resource_agent_id
        if session and session.exists():
            return session
        session = Session.search([("chat_key", "=", chat_key)], limit=1)
        if not session:
            cwd = (runner.default_cwd or "").rstrip("/")
            session = Session.create({
                "session_id": str(uuid.uuid4()),
                "name": "Resource management — %s" % runner.name,
                "title_source": "custom",
                "runner_id": runner.id,
                "cwd": (cwd + "/resources-agent") if cwd else False,
                "start_ts": fields.Datetime.now(),
                "chat_key": chat_key,
                "live_permission_mode": "dontAsk",
                "live_model": runner.default_model or "default",
                "is_resource_agent": True,
            })
            session.write({"following": True})
        runner.write({"resource_agent_id": session.id})
        return session

    @api.model
    def fd_resource_chat(self, runner_id, text, selected_keys=None):
        self._require_manager()
        if not isinstance(text, str) or not text.strip():
            raise UserError(_("Type a message first."))
        text = text.strip()
        if len(text) > CHAT_MAX_CHARS:
            raise UserError(_("Messages are limited to %d characters.") % CHAT_MAX_CHARS)
        keys = []
        if selected_keys is not None:
            if not isinstance(selected_keys, (list, tuple)) or len(selected_keys) > CHAT_MAX_KEYS:
                raise UserError(_("selected_keys must be a list of at most %d keys.") % CHAT_MAX_KEYS)
            for key in selected_keys:
                if not isinstance(key, str) or len(key) > KEY_MAX_CHARS or not valid_resource_key(key):
                    raise UserError(_("Invalid resource key in selection."))
                keys.append(key)
        runner = self._resource_runner(runner_id)
        session = runner._resource_agent_session(runner)
        if session.runtime_state in ("busy", "starting"):
            raise UserError(_("The management session is %s; wait for it to settle.") % session.runtime_state)
        snapshot = runner._connector().resource_snapshot()
        prompt = build_resource_chat_prompt(snapshot, text, keys)
        last = self.env["flightdeck.message.text"].search(
            [("session_ref_id", "=", session.id)], order="seq desc", limit=1)
        reply = self.env["flightdeck.session.reply"].create({
            "session_ref_id": session.id,
            "key": "resource.chat:" + uuid.uuid4().hex,
            "prompt": prompt,
            "resource_operator_prompt": text,
            "after_seq": last.seq if last else -1,
        })
        session.send_message(prompt)
        return {"reply_id": reply.id, "session_uuid": session.session_id}

    @api.model
    def fd_propose_resource_action(self, runner_id, action_kind, resource_key, reason=""):
        self._require_manager()
        if not valid_action_kind(action_kind):
            raise UserError(_("Unknown action kind %s.") % action_kind)
        if not valid_resource_key(resource_key):
            raise UserError(_("Invalid resource key."))
        runner = self._resource_runner(runner_id)
        snapshot = runner._connector().resource_snapshot()
        if not snapshot.get("complete") or not snapshot.get("available"):
            raise UserError(_("The resource inventory is unavailable; nothing can be confirmed."))
        if not resource_snapshot_fresh(snapshot):
            raise UserError(_("The inventory is stale; rescan before confirming."))
        action = self.env["flightdeck.resource.action"]._create_from_proposal(
            runner, {"proposal_id": "manual:" + uuid.uuid4().hex, "action_kind": action_kind,
                     "resource_key": resource_key, "reason": reason or "",
                     "requested_by": self.env.user.id}, snapshot)
        return action.id

    @api.model
    def fd_confirm_resource_action(self, action_id):
        self._require_manager()
        Action = self.env["flightdeck.resource.action"]
        action = Action.browse(int(action_id)).exists()
        if not action:
            return {"ok": False, "error": "unknown action"}
        self.env.cr.execute(
            "SELECT id FROM flightdeck_resource_action WHERE id = %s FOR UPDATE", (action.id,))
        self.env.invalidate_all()
        action = Action.browse(action.id)
        if action.state != "pending":
            return {"ok": False, "error": "only a pending proposal can be confirmed", "state": action.state}
        if action.expires_at and action.expires_at <= fields.Datetime.now():
            action.write({"state": "expired"})
            return {"ok": False, "error": "expired", "state": "expired"}
        runner = action.runner_id
        if not runner.active:
            action.write({"state": "failed", "result": "runner inactive"})
            return {"ok": False, "error": "runner inactive", "state": "failed"}
        snapshot = runner._connector().resource_snapshot()
        if not resource_snapshot_fresh(snapshot):
            return {"ok": False, "error": "inventory is stale; rescan first"}
        if action.action_kind == "stop_process":
            row = next((r for r in snapshot.get("processes", [])
                        if r.get("key") == action.resource_key), None)
            if row is None:
                return {"ok": False, "error": "the process is gone"}
            recorded = action.expected_identity or {}
            live = expected_from_row(row)
            if any(recorded.get(f) != live.get(f) for f in PROCESS_IDENTITY_FIELDS):
                return {"ok": False, "error": "the process identity changed since the proposal"}
        else:
            row = next((r for r in snapshot.get("mcp_servers", [])
                        if r.get("key") == action.resource_key), None)
            if row is None or row.get("session_id") != action.session_uuid \
                    or row.get("name") != action.server_name:
                return {"ok": False, "error": "the MCP server is no longer what the proposal saw"}
        action.write({
            "state": "queued",
            "confirmed_by": self.env.user.id,
            "confirmed_at": fields.Datetime.now(),
        })
        runner.with_delay(
            description="FlightDeck: apply a resource action",
            identity_key="resource-action:%s" % action.command_uuid,
        )._dispatch_resource_action(action.id)
        return {"ok": True, "state": "queued"}

    @api.model
    def fd_deny_resource_action(self, action_id):
        self._require_manager()
        action = self.env["flightdeck.resource.action"].browse(int(action_id)).exists()
        if not action:
            raise UserError(_("Unknown action."))
        if action.state != "pending":
            raise UserError(_("Only a pending proposal can be denied."))
        action.write({"state": "denied"})
        return True

    def _dispatch_resource_action(self, action_id):
        self.ensure_one()
        action = self.env["flightdeck.resource.action"].browse(action_id).exists()
        if not action or action.runner_id != self:
            _logger.warning("resource action %s is gone", action_id)
            return False
        if action.state != "queued":
            return False
        if not self.env.user.has_group("flight_deck.group_manager"):
            action.write({"state": "failed", "result": "the confirming user is not a FlightDeck manager"})
            return False
        try:
            self.check_access("write")
            action.check_access("write")
        except AccessError:
            action.write({"state": "failed", "result": "access was revoked before the action ran"})
            return False
        if not self.active or self.kind != "spool":
            action.write({"state": "failed", "result": "runner unavailable"})
            return False
        if not action.confirmed_by:
            action.write({"state": "failed", "result": "never confirmed"})
            return False
        if action.expires_at and action.expires_at <= fields.Datetime.now():
            action.write({"state": "expired"})
            return False
        remaining = action.expires_at - fields.Datetime.now()
        row = self._submit("resource_apply", session=None, expires_in=remaining,
                           command_id=action.command_uuid, args={
                               "action_id": action.command_uuid,
                               "action_kind": action.action_kind,
                               "resource_key": action.resource_key,
                               "expected": action.expected_identity or {},
                               "session_ref": action.session_uuid or "",
                               "server_name": action.server_name or "",
                           }, defer_submit=True)
        action.write({"state": "submitted", "command_id": row.id if row else False})
        return True

    def _apply_event(self, event):
        result = super()._apply_event(event)
        kind = event.get("kind")
        if kind == "resource_proposal":
            self._apply_resource_proposal(event.get("payload") or {})
        elif kind == "resource_action":
            self._apply_resource_result(event.get("payload") or {})
        return result

    def _apply_resource_proposal(self, payload):
        proposal_id = payload.get("proposal_id")
        if not proposal_id:
            return
        Action = self.env["flightdeck.resource.action"]
        if Action.search(
                [("runner_id", "=", self.id), ("proposal_id", "=", proposal_id)], limit=1):
            return
        snapshot = self._connector().resource_snapshot()
        try:
            Action._create_from_proposal(self, payload, snapshot)
        except UserError as e:
            _logger.warning("resource proposal %s rejected: %s", proposal_id, e)

    def _apply_resource_result(self, payload):
        action_id = payload.get("action_id")
        if not action_id:
            return
        action = self.env["flightdeck.resource.action"].search(
            [("runner_id", "=", self.id), ("command_uuid", "=", action_id)], limit=1)
        if not action:
            _logger.warning("resource_action for unknown command %s", action_id)
            return
        state = payload.get("state") or "failed"
        if state not in ("completed", "failed", "uncertain", "still_running"):
            state = "uncertain"
        action.write({
            "state": state,
            "result": payload.get("result") or payload.get("error") or False,
        })


class FlightdeckResourceAction(models.Model):
    _name = "flightdeck.resource.action"
    _description = "Confirmed resource action"
    _order = "id desc"

    runner_id = fields.Many2one("flightdeck.runner", required=True, ondelete="cascade", index=True)
    proposal_id = fields.Char(required=True, index=True, readonly=True)
    action_kind = fields.Selection(RESOURCE_ACTIONS, required=True, readonly=True)
    resource_key = fields.Char(required=True, readonly=True)
    expected_identity = fields.Json(readonly=True)
    session_uuid = fields.Char(readonly=True)
    server_name = fields.Char(readonly=True)
    reason = fields.Text(readonly=True)
    state = fields.Selection([
        ("pending", "Pending"), ("queued", "Queued"), ("submitted", "Submitted"),
        ("completed", "Completed"), ("failed", "Failed"), ("uncertain", "Uncertain"),
        ("still_running", "Still running"), ("denied", "Denied"), ("expired", "Expired"),
    ], default="pending", required=True, readonly=True, index=True)
    expires_at = fields.Datetime(readonly=True)
    command_uuid = fields.Char(readonly=True, index=True)
    command_id = fields.Many2one("flightdeck.runner.command", readonly=True)
    result = fields.Text(readonly=True)
    requested_by = fields.Many2one("res.users", readonly=True)
    confirmed_by = fields.Many2one("res.users", readonly=True)
    confirmed_at = fields.Datetime(readonly=True)

    _runner_proposal_uniq = models.UniqueIndex(
        "(runner_id, proposal_id)", "This proposal is already recorded for the runner.")
    _runner_command_uniq = models.UniqueIndex(
        "(runner_id, command_uuid)", "This command UUID is already recorded for the runner.")

    @api.model
    def _create_from_proposal(self, runner, payload, snapshot):
        kind = payload.get("action_kind")
        key = payload.get("resource_key")
        if not valid_action_kind(kind):
            raise UserError(_("Unknown action kind %s.") % kind)
        if not valid_resource_key(key):
            raise UserError(_("Invalid resource key."))
        if not isinstance(snapshot, dict) or not snapshot.get("complete"):
            raise UserError(_("The resource inventory is unavailable."))
        expected, session_uuid, server_name = {}, "", ""
        if kind == "stop_process":
            row = next((r for r in snapshot.get("processes", []) if r.get("key") == key), None)
            if row is None:
                raise UserError(_("The process is no longer in the inventory."))
            if row.get("protected") or not row.get("manageable"):
                raise UserError(_("That process is protected or not manageable."))
            expected = expected_from_row(row)
            err = validate_expected_process(expected)
            if err:
                raise UserError(_("The process identity is incomplete: %s.") % err)
            proposed = payload.get("expected") or {}
            if proposed and any(proposed.get(f) is not None and proposed.get(f) != expected.get(f)
                                for f in expected):
                raise UserError(_("The proposed identity no longer matches the live process."))
        else:
            row = next((r for r in snapshot.get("mcp_servers", []) if r.get("key") == key), None)
            if row is None:
                raise UserError(_("The MCP server is not in the latest status."))
            if row.get("protected"):
                raise UserError(_("That MCP server is protected."))
            session_uuid = row.get("session_id") or ""
            server_name = row.get("name") or ""
            if payload.get("session_id") and payload["session_id"] != session_uuid:
                raise UserError(_("The proposed session no longer owns this server."))
            if payload.get("server_name") and payload["server_name"] != server_name:
                raise UserError(_("The proposed server name does not match the latest status."))
            expected = {"key": key, "session_id": session_uuid, "server_name": server_name}
        due = fields.Datetime.now() + timedelta(seconds=RESOURCE_ACTION_TTL_SECONDS)
        if payload.get("expires_at"):
            expires, err = parse_expiry(payload["expires_at"])
            if err or not expires:
                raise UserError(_("The proposed expiry is malformed: %s.") % (err or "invalid"))
            due = min(due, expires.replace(tzinfo=None))
        return self.create({
            "runner_id": runner.id,
            "proposal_id": payload["proposal_id"],
            "action_kind": kind,
            "resource_key": key,
            "expected_identity": expected,
            "session_uuid": session_uuid,
            "server_name": server_name,
            "reason": payload.get("reason") or False,
            "expires_at": due,
            "command_uuid": str(uuid.uuid4()),
            "requested_by": payload.get("requested_by") or False,
        })


class FlightdeckSessionReplyResources(models.Model):
    _inherit = "flightdeck.session.reply"

    resource_operator_prompt = fields.Text(readonly=True)

    def _answer_text(self):
        self.ensure_one()
        if not self.resource_operator_prompt:
            return super()._answer_text()
        lines = self.env["flightdeck.message.text"].search(
            [("session_ref_id", "=", self.session_ref_id.id),
             ("seq", ">", self.after_seq)], order="seq")
        prompt_seen, parts = False, []
        for line in lines:
            if line.role == "user" and line.text and not line.tool_name:
                if prompt_seen:
                    break
                if line.text == self.prompt:
                    prompt_seen = True
                continue
            if prompt_seen and line.role == "assistant" and line.text and not line.tool_name:
                parts.append(line.text)
        return "\n\n".join(parts) if parts else None


class FlightdeckSessionResources(models.Model):
    _inherit = "flightdeck.session"

    is_resource_agent = fields.Boolean(readonly=True, index=True)

    def _start_args(self):
        args = super()._start_args()
        if self.is_resource_agent:
            args["resource_agent"] = True
            args["append_system_prompt"] = RESOURCE_AGENT_INSTRUCTIONS
            args["permission_mode"] = "dontAsk"
            args["allowed_tools"] = []
        return args

    def write(self, vals):
        protected = {"live_permission_mode", "live_model", "append_system_prompt", "is_resource_agent"}
        if any(s.is_resource_agent for s in self) and protected & vals.keys():
            raise UserError(_("The resource management session's profile is fixed."))
        return super().write(vals)
