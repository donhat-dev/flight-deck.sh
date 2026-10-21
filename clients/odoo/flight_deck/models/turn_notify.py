"""Tell a person, by web push, when an agent finishes a turn, asks them a question
(`AskUserQuestion`), or waits for their approval.

The push goes out through Odoo's own queue (`mail.push` and its cron), after the
transaction that noticed the event commits; the browser's service worker shows it
as an operating-system notification. A click opens the session's Logbook form, or the
approval request.
"""
import json
from datetime import timedelta

from odoo import fields, models

# A turn shorter than this was most likely watched while it ran.
MIN_TURN = timedelta(seconds=30)
# A turn read this long after it ended (a backlog, a first read) is not news.
FRESH = timedelta(minutes=10)
ICON = "/web/static/img/odoo-icon-192x192.png"
BODY_CHARS = 160


class ResUsersAgentNotify(models.Model):
    _inherit = "res.users"

    fd_notify_turns = fields.Boolean(
        "Agent notifications", default=True,
        help="Notify me when an agent finishes a turn or waits for my approval.")

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["fd_notify_turns"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["fd_notify_turns"]


class FlightdeckTurnNotify(models.Model):
    _inherit = "flightdeck.turn"

    notified_at = fields.Datetime(
        readonly=True, help="When the person was told the agent finished this turn.")


class FlightdeckSessionNotify(models.Model):
    _inherit = "flightdeck.session"

    notify_turns = fields.Boolean(
        "Notify", default=True,
        help="Send a notification when this session's agent finishes a turn, asks a "
             "question, or waits for approval.")

    def action_toggle_notify(self):
        for session in self:
            session.notify_turns = not session.notify_turns
        return True

    def _store_turns(self, opened, closed=None):
        super()._store_turns(opened, closed)
        # `ended` is the agent's end_turn; an interrupted turn has only `last`.
        ended = [p for p, end in (closed or {}).items() if end.get("ended")]
        if ended:
            self._notify_turns_ended(ended)
        asked = {p: end["asked"] for p, end in (closed or {}).items() if end.get("asked")}
        if asked:
            self._notify_questions(asked)

    def _notify_questions(self, asked):
        """Push "waiting for your answer" for a fresh question, asked far enough into its
        turn that the person may have left; `asked` maps prompt ids to {at, question}."""
        self.ensure_one()
        now = fields.Datetime.now()
        turns = self.env["flightdeck.turn"].search([
            ("prompt_id", "in", list(asked)), ("session_ref_id", "=", self.id)])
        for turn in turns:
            at = asked[turn.prompt_id]["at"].replace(tzinfo=None)
            if at < now - FRESH or not turn.started_at or at - turn.started_at < MIN_TURN:
                continue
            self._push_to_owners(
                "Waiting for your answer: %s" % self.display_name,
                asked[turn.prompt_id]["question"] or turn.prompt_head or "",
                "flightdeck.session", self.id, "fd-ask-%d" % turn.id)

    def _notify_turns_ended(self, prompt_ids):
        """Push "agent finished" once per turn, for fresh turns long enough to leave."""
        self.ensure_one()
        now = fields.Datetime.now()
        turns = self.env["flightdeck.turn"].search([
            ("prompt_id", "in", prompt_ids), ("session_ref_id", "=", self.id),
            ("notified_at", "=", False), ("ended_at", ">=", now - FRESH)])
        turns = turns.filtered(
            lambda t: t.started_at and t.ended_at - t.started_at >= MIN_TURN)
        for turn in turns:
            self._push_to_owners(
                "Agent finished: %s" % self.display_name,
                turn.final_head or turn.prompt_head or "",
                "flightdeck.session", self.id, "fd-turn-%d" % turn.id)
        turns.write({"notified_at": now})

    def _push_to_owners(self, title, body, model, res_id, tag):
        """Queue one push per browser of each FlightDeck user who wants them, unless
        this session's notifications are off."""
        self.ensure_one()
        if not self.notify_turns:
            return
        users = self.env["res.users"].sudo().search([
            ("fd_notify_turns", "=", True),
            ("all_group_ids", "in", self.env.ref("flight_deck.group_user").ids)])
        devices = self.env["mail.push.device"].sudo().search(
            [("partner_id", "in", users.partner_id.ids)])
        if not devices:
            return
        body = " ".join((body or "").split())
        payload = json.dumps({"title": title[:120], "options": {
            "body": body[:BODY_CHARS] + ("…" if len(body) > BODY_CHARS else ""),
            "icon": ICON,
            # The same tag replaces an earlier notification of the same thing.
            "tag": tag,
            "data": {"model": model, "res_id": res_id},
        }}, ensure_ascii=False)
        self.env["mail.push"].sudo().create(
            [{"mail_push_device_id": d.id, "payload": payload} for d in devices])
        self.env.ref("mail.ir_cron_web_push_notification")._trigger()


class FlightdeckDecisionNotify(models.Model):
    _inherit = "flightdeck.decision"

    def _from_event(self, session, payload):
        new = not self.search_count([("request_id", "=", payload.get("request_id"))])
        record = super()._from_event(session, payload)
        if new:
            session._push_to_owners(
                "Waiting for your approval: %s" % session.display_name,
                "%s: %s" % (record.tool_name, record.summary or ""),
                "flightdeck.decision", record.id, "fd-decision-%d" % record.id)
        return record
