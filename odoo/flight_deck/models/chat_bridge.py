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
            reply.write({"state": "answered", "reply": text, "answered_at": now})
            runs = Wait._resume_by_key(reply.key, {"text": text, "session_id": reply.session_ref_id.session_id})
            _logger.info("Session %s answered reply %s; resumed runs %s.", reply.session_ref_id.id, reply.id, runs.ids)


class FlightdeckSession(models.Model):
    _inherit = "flightdeck.session"

    chat_key = fields.Char(
        index=True, copy=False,
        help="The outside conversation this session answers, e.g. zalo:<chat id>. Set by fd_chat_send.")
    append_system_prompt = fields.Text(
        help="Added to the system prompt when the runner starts this session.")
    reply_ids = fields.One2many("flightdeck.session.reply", "session_ref_id", string="Reply Waits")

    _chat_key_uniq = models.UniqueIndex("(chat_key) WHERE chat_key IS NOT NULL", "Another session already answers this chat.")

    def _start_args(self):
        args = super()._start_args()
        if self.append_system_prompt:
            args["append_system_prompt"] = self.append_system_prompt
        return args

    @api.model
    def fd_chat_send(self, chat_key, text, name=None, runner_id=None):
        """Send *text* to the session of *chat_key*, made on first use on
        *runner_id* or the container runner, and return the key a Wait step
        waits on for the answer."""
        chat_key = (chat_key or "").strip()
        if not chat_key:
            raise UserError(_("A chat needs a key, e.g. zalo:<chat id>."))
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
                # Never asks and allows nothing beyond the runner's rules: a chat cannot approve a tool.
                "live_permission_mode": "dontAsk",
                "append_system_prompt": CHAT_PROMPT,
            })
            # A write, not a create flag: write() is what arms the follow offset.
            session.write({"following": True})
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
            if reply:
                reply.write({"state": "ended", "ended_at": fields.Datetime.now()})
        return result
