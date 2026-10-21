"""Ask a session from outside Flightdoo and get its answer back in a flow.

A Workflow Studio flow calls `flightdeck.session.fd_chat_send(chat_key, text)`
in a code step. The chat's session (one per chat key, made on first use) gets
the text as a user turn, and the call returns a key the flow's Wait step
waits on. When the runner reports the turn's `result`, the reply row is
marked ended; once the follow cron has read the answer from the transcript,
the Wait is resumed with `{"text", "session_id"}`.

The answer comes from the transcript, not the runner event: the event only
says the turn is over, and the cron reads events before transcripts.
"""
import logging
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

KEY_PREFIX = "flightdeck.reply:"
# An ended turn whose answer has not appeared in the transcript by then is given up.
ANSWER_GRACE = timedelta(minutes=5)
CHAT_PROMPT = (
    "You are answering a person in a chat app. Reply in plain text in the language they write in, "
    "briefly, without markdown tables or headings. You have no tools here."
)
# User turns the runner sends itself (runner/control.go SetEffort), as typed or
# as the CLI tags a slash command in the transcript.
RUNNER_COMMANDS = ("/effort ", "<command-name>/effort</command-name>")
# Start arguments a chat session may add for the runner (runner/start_options.go).
START_OPTIONS = ("json_schema", "tools", "max_budget_usd", "max_turns")
# The permission modes a chat session may be given; never bypassPermissions.
BRIDGE_MODES = ("dontAsk", "default", "acceptEdits", "plan", "auto")


class FlightdeckSessionReply(models.Model):
    _name = "flightdeck.session.reply"
    _description = "Session Reply Wait"
    _order = "id"

    session_ref_id = fields.Many2one("flightdeck.session", "Session", required=True, index=True, ondelete="cascade")
    key = fields.Char(required=True, index=True, readonly=True,
                      help="The correlation key the flow's Wait step waits on.")
    prompt = fields.Text(readonly=True)
    after_seq = fields.Integer(readonly=True, help="Last transcript line of the session before this prompt.")
    state = fields.Selection(
        [("waiting", "Waiting"), ("ended", "Turn ended"), ("answered", "Answered"), ("expired", "No answer")],
        default="waiting", required=True, index=True, readonly=True)
    reply = fields.Text(readonly=True)
    ended_at = fields.Datetime(readonly=True)
    answered_at = fields.Datetime(readonly=True)

    def _answer_text(self):
        """The session's answer to this prompt: the assistant text after the
        prompt's line and before the next prompt. None while it is not read yet."""
        self.ensure_one()
        lines = self.env["flightdeck.message.text"].search(
            [("session_ref_id", "=", self.session_ref_id.id), ("seq", ">", self.after_seq)], order="seq")
        prompt_seen, parts = False, []
        for line in lines:
            if line.role == "user" and line.text and not line.tool_name:
                if line.text.startswith(RUNNER_COMMANDS):
                    # The runner's own turn (an effort change) is not the prompt; it is
                    # sent before the prompt, so its reply comes before the prompt too.
                    continue
                if prompt_seen:
                    break
                prompt_seen = True
                continue
            if prompt_seen and line.role == "assistant" and line.text and not line.tool_name:
                parts.append(line.text)
        return "\n\n".join(parts) if parts else None

    def _resume(self):
        """Hand each ended turn's answer to the flow waiting on it."""
        Wait = self.env["workflow.run.wait"]
        now = fields.Datetime.now()
        for reply in self:
            text = reply._answer_text()
            if text is None:
                if reply.ended_at and reply.ended_at < now - ANSWER_GRACE:
                    reply.write({"state": "expired"})
                    _logger.warning("Session %s: no answer found for reply %s.", reply.session_ref_id.id, reply.id)
                continue
            reply._answer(text)

    def _answer(self, text, extra=None):
        """Mark this reply answered and resume the flow waiting on it."""
        self.ensure_one()
        now = fields.Datetime.now()
        self.write({"state": "answered", "reply": text, "ended_at": self.ended_at or now, "answered_at": now})
        payload = dict(extra or {}, text=text, session_id=self.session_ref_id.session_id)
        runs = self.env["workflow.run.wait"]._resume_by_key(self.key, payload)
        _logger.info("Session %s answered reply %s; resumed runs %s.", self.session_ref_id.id, self.id, runs.ids)

    def _answer_from_result(self, payload):
        """Answer from the runner's result event, which carries the turn's final
        text, its structured output and its usage since fd-runner 2026-10-13."""
        usage = payload.get("usage") or {}
        is_error = bool(payload.get("is_error"))
        text = payload.get("result") or ""
        if is_error and not text:
            text = payload.get("subtype") or "error"
        self._answer(text, {
            "is_error": is_error,
            # success, or why the turn stopped: error_max_turns, error_max_budget_usd, ...
            # A failed sign-in ends with subtype "success" and is_error: say error.
            "status": "error" if is_error and payload.get("subtype") in (None, "", "success")
                      else payload.get("subtype") or "success",
            "data": payload.get("structured_output"),
            "usage": {
                "cost_usd": payload.get("total_cost_usd") or 0.0,
                "input_tokens": usage.get("input_tokens") or 0,
                "output_tokens": usage.get("output_tokens") or 0,
                "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
                "turns": payload.get("num_turns") or 0,
                "duration_ms": payload.get("duration_ms") or 0,
            },
        })


class FlightdeckSession(models.Model):
    _inherit = "flightdeck.session"

    chat_key = fields.Char(
        index=True, copy=False,
        help="The outside conversation this session answers, e.g. zalo:<chat id>. Set by fd_chat_send.")
    append_system_prompt = fields.Text(
        help="Added to the system prompt when the runner starts this session.")
    reply_ids = fields.One2many("flightdeck.session.reply", "session_ref_id", string="Reply Waits")
    start_options = fields.Json(
        copy=False,
        help="What the runner starts this session with besides the runner's own settings: "
             "json_schema, tools, max_budget_usd. Set by fd_chat_send.")

    _chat_key_uniq = models.UniqueIndex("(chat_key) WHERE chat_key IS NOT NULL", "Another session already answers this chat.")

    def _start_args(self):
        args = super()._start_args()
        if self.append_system_prompt:
            args["append_system_prompt"] = self.append_system_prompt
        options = self.start_options or {}
        args.update({k: v for k, v in options.items() if k in START_OPTIONS})
        # Rules the caller allows on top of the runner's, for a session in dontAsk.
        allow = [rule for rule in options.get("allow") or [] if isinstance(rule, str) and rule]
        if allow:
            args["allowed_tools"] = list(dict.fromkeys((args.get("allowed_tools") or []) + allow))
        return args

    @api.model
    def fd_chat_send(self, chat_key, text, name=None, runner_id=None, model=None, effort=None,
                     instructions=None, options=None, mode=None):
        """Send *text* to the session of *chat_key*, made on first use on
        *runner_id* or the container runner, and return the key a Wait step
        waits on for the answer. *model* and *effort*, when given, apply from
        this turn on; "" means the runner's default and None keeps what the
        session has. *mode* is the permission mode (dontAsk when the session is
        made without one).

        *instructions* replace the chat prompt and *options* (json_schema,
        tools, max_budget_usd, max_turns, and ``allow``: permission rules added
        to the runner's) are fixed when the session is made: they hold for its
        whole process, so a caller that needs others uses another key."""
        chat_key = (chat_key or "").strip()
        if not chat_key:
            raise UserError(_("A chat needs a key, e.g. zalo:<chat id>."))
        if mode is not None and mode not in BRIDGE_MODES:
            # A bridge caller never skips the permission check (bypassPermissions).
            raise UserError(_("A chat session cannot run in permission mode %s.") % mode)
        session = self.search([("chat_key", "=", chat_key)], limit=1)
        if not session:
            Runner = self.env["flightdeck.runner"]
            runner = Runner.browse(runner_id).exists() if runner_id else Runner.search(
                [("zone", "=", "container"), ("active", "=", True)], limit=1)
            if not runner:
                raise UserError(_("No active container runner to start a chat session on."))
            session = self.create({
                "session_id": str(uuid.uuid4()),
                "name": name or chat_key,
                "title_source": "custom",
                "runner_id": runner.id,
                "cwd": runner.default_cwd or False,
                "start_ts": fields.Datetime.now(),
                "chat_key": chat_key,
                # Without a mode it never asks and allows nothing beyond the runner's
                # rules: a chat cannot approve a tool. A flow step picks its own.
                "live_permission_mode": mode or "dontAsk",
                "append_system_prompt": instructions or CHAT_PROMPT,
                "start_options": {k: v for k, v in (options or {}).items()
                                  if k in START_OPTIONS or k == "allow"} or False,
            })
            # A write, not a create flag: write() is what arms the follow offset.
            session.write({"following": True})
        # "" asks for the runner's default; None keeps what the session has. A runner
        # without a default effort leaves the session's, since a level cannot be unset.
        if model == "":
            model = session.runner_id.default_model or "default"
        if effort == "":
            effort = session.runner_id.default_effort or None
        # Only a change is written: a write on a live session sends the runner a command.
        picks = {"live_model": model, "live_effort": effort, "live_permission_mode": mode}
        changed = {field: value for field, value in picks.items() if value and session[field] != value}
        if changed:
            session.write(changed)
        last = self.env["flightdeck.message.text"].search(
            [("session_ref_id", "=", session.id)], order="seq desc", limit=1)
        key = KEY_PREFIX + uuid.uuid4().hex
        self.env["flightdeck.session.reply"].create({
            "session_ref_id": session.id, "key": key, "prompt": text, "after_seq": last.seq if last else -1})
        session.send_message(text)
        return key

    @api.model
    def _cron_follow(self):
        result = super()._cron_follow()
        ended = self.env["flightdeck.session.reply"].search([("state", "=", "ended")])
        if ended:
            ended._resume()
        return result


class FlightdeckRunner(models.Model):
    _inherit = "flightdeck.runner"

    def _apply_event(self, event):
        result = super()._apply_event(event)
        if event.get("kind") == "result" and event.get("session_id"):
            session = self.env["flightdeck.session"].search([("session_id", "=", event["session_id"])], limit=1)
            # Turns end in the order they were sent: the oldest waiting reply is this turn's.
            reply = session.reply_ids.filtered(lambda r: r.state == "waiting")[:1]
            payload = event.get("payload") or {}
            if payload.get("command"):
                # A turn the runner sent itself (/effort): no prompt's answer.
                return result
            if reply and "result" in payload:
                reply._answer_from_result(payload)
            elif reply:
                # An older runner: the follow cron reads the answer from the transcript.
                reply.write({"state": "ended", "ended_at": fields.Datetime.now()})
        return result
