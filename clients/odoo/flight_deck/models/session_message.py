"""Messages Claude sessions send each other, read from both ends.

A sending session writes a SendMessage call and, on a later line, the answer
that names the message id. The receiving session writes the message itself,
with the same id, either queued into the turn it was running or as a turn of
its own. One row per id joins the two ends, so a row knows who sent what to
whom, when it left and when it arrived.

Rows that link the same two sessions form a conversation. Within it, a row
answering the other side points at the message it answers, and how long the
answer took is what an analysis of work between sessions is built on.

Kept off `flightdeck.session`: the scan has its own cursor model, because the
follow cron writes the session every minute and two crons writing one row
take turns failing on concurrent updates.
"""
import json
import logging
import os

from odoo import api, fields, models

from . import transcript_reader
from .session import ROSTER_ROOT, _stamp

_logger = logging.getLogger(__name__)

# Most bytes one cron run reads. The first run reads every transcript on disk
# (about 2 GB, measured at 3 s with a warm cache); this caps one run when the
# cache is cold.
SCAN_BUDGET = 1024 * 1024 * 1024
SOCKET_PREFIX = "uds:"
NEVER = "9999"


def read_peers(root=ROSTER_ROOT):
    """`{socket path: session id}` and `{name: [session id]}` of live sessions.

    Only processes running now are listed, so this names the other end of a
    message only while it is still open.
    """
    sockets, names = {}, {}
    try:
        files = os.listdir(root)
    except OSError:
        return sockets, names
    for file in files:
        if not file.endswith(".json"):
            continue
        try:
            with open(os.path.join(root, file), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("sessionId"):
            continue
        if data.get("messagingSocketPath"):
            sockets[data["messagingSocketPath"]] = data["sessionId"]
        if data.get("name"):
            names.setdefault(data["name"], []).append(data["sessionId"])
    return sockets, names


class FlightdeckSessionMessageScan(models.Model):
    """How far the message scan has read one session's transcript."""

    _name = "flightdeck.session.message.scan"
    _description = "Session message scan"

    session_ref_id = fields.Many2one("flightdeck.session", required=True, index=True,
                                     ondelete="cascade")
    read_bytes = fields.Integer()
    # The first timestamp in the file. A forked session copies the lines it
    # inherited, so the same message sits in several files; the file that
    # starts first is the original.
    first_ts = fields.Datetime()

    _session_uniq = models.Constraint("unique(session_ref_id)", "One scan per session.")

    @api.model
    def _cron_read(self, budget=SCAN_BUDGET):
        """Read what every transcript wrote since the last run."""
        Session = self.env["flightdeck.session"]
        index = Session._transcript_index()
        cursors = {c.session_ref_id.id: c for c in self.search([])}
        for session in Session.search([("session_id", "in", list(index))], order="id"):
            if budget <= 0:
                break
            path = index[session.session_id]
            cursor = cursors.get(session.id)
            try:
                if os.path.getsize(path) == (cursor.read_bytes if cursor else 0):
                    continue
                budget -= self._read_session(session, path, cursor, budget)
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception("reading session messages failed for session %s", session.id)
        return True

    def _read_session(self, session, path, cursor=None, limit=None):
        """Read one transcript from its cursor; returns the bytes read."""
        offset = cursor.read_bytes if cursor else 0
        if os.path.getsize(path) < offset:
            # Rewritten: the offset means nothing. Reading it again is safe,
            # every event is keyed on what it already names.
            offset = 0
        pending = self.env["flightdeck.session.message"].search([
            ("sender_session_id", "=", session.id), ("msg_id", "=", False),
            ("send_error", "=", False)]).mapped("send_tool_use_id")
        events, consumed, first_ts = transcript_reader.scan_peer_messages(
            path, offset, limit, pending=pending)
        vals = {"read_bytes": offset + consumed}
        if first_ts:
            vals["first_ts"] = _stamp(first_ts)
        if cursor:
            cursor.write(vals)
        else:
            self.create(dict(vals, session_ref_id=session.id))
        if events:
            self.env["flightdeck.session.message"]._ingest(session, events)
        return consumed


class FlightdeckSessionMessage(models.Model):
    _name = "flightdeck.session.message"
    _description = "Session message"
    _order = "sent_at desc, id desc"

    name = fields.Char(compute="_compute_name")
    msg_id = fields.Char("Message id", index=True, readonly=True)
    sender_session_id = fields.Many2one("flightdeck.session", "From", index=True,
                                        ondelete="set null", readonly=True)
    sender_name = fields.Char("From name", readonly=True,
                              help="The name the receiving session saw it under.")
    sender_socket = fields.Char(readonly=True)
    recipient_session_id = fields.Many2one("flightdeck.session", "To", index=True,
                                           ondelete="set null", readonly=True)
    recipient_label = fields.Char("To name", readonly=True,
                                  help="What the sending session wrote as the recipient.")
    summary = fields.Char(readonly=True)
    body = fields.Text(readonly=True)
    sent_at = fields.Datetime("Sent", index=True, readonly=True)
    received_at = fields.Datetime("Received", readonly=True)
    delivery = fields.Selection(
        [("turn", "Started a turn"), ("queued", "Queued into a turn")], readonly=True,
        help="Started a turn: the session was idle. Queued: it was working, and read it mid-turn.")
    send_tool_use_id = fields.Char(index=True, readonly=True)
    send_uuid = fields.Char(readonly=True)
    receive_uuid = fields.Char(readonly=True)
    thread_id = fields.Many2one("flightdeck.session.thread", "Conversation", index=True,
                                ondelete="set null", readonly=True)
    sequence = fields.Integer(readonly=True, help="Place in the conversation, from 1.")
    reply_to_id = fields.Many2one("flightdeck.session.message", "Answers", ondelete="set null",
                                  readonly=True,
                                  help="The latest earlier message the other session sent here.")
    reply_minutes = fields.Float("Reply time (min)", digits=(10, 1), readonly=True,
                                 help="From the arrival of the message it answers to this send.")
    delivery_seconds = fields.Float("Delivery (s)", digits=(10, 1), readonly=True,
                                    help="From the send to the arrival.")
    send_error = fields.Char("Refused", readonly=True, help="What the tool answered a send it refused.")
    state = fields.Selection(
        [("pending", "Waiting for the answer"), ("failed", "Refused"),
         ("sent", "Sent, not seen arriving"), ("received", "Received"),
         ("delivered", "Sent and received")],
        compute="_compute_state", store=True, index=True)

    _msg_uniq = models.Constraint("unique(msg_id)", "Message already recorded.")
    _send_uniq = models.Constraint("unique(sender_session_id, send_tool_use_id)",
                                   "Send already recorded.")

    @api.depends("summary", "body")
    def _compute_name(self):
        for rec in self:
            rec.name = rec.summary or (rec.body or "").strip().split("\n", 1)[0][:80] or rec.msg_id

    @api.depends("msg_id", "send_tool_use_id", "receive_uuid", "send_error")
    def _compute_state(self):
        for rec in self:
            if rec.send_error:
                rec.state = "failed"
            elif rec.send_tool_use_id and rec.receive_uuid:
                rec.state = "delivered"
            elif rec.receive_uuid:
                rec.state = "received"
            elif rec.msg_id:
                rec.state = "sent"
            else:
                rec.state = "pending"

    # --- reading ------------------------------------------------------------
    @api.model
    def _ingest(self, session, events):
        """Store what one transcript said about messages, in file order."""
        touched = self.browse()
        for event in events:
            kind = event["kind"]
            if kind == "send":
                touched |= self._on_send(session, event)
            elif kind == "sent":
                touched |= self._on_sent(session, event)
            elif kind == "answered":
                touched |= self._on_answered(session, event)
            elif kind == "received":
                touched |= self._on_received(session, event)
        touched = touched.exists()
        touched._resolve_from_roster()
        touched._thread()
        return touched

    def _sender_values(self, session, event):
        return {
            "sender_session_id": session.id,
            "send_tool_use_id": event["tool_use_id"],
            "send_uuid": event.get("uuid") or False,
            "sent_at": _stamp(event.get("ts")),
            "recipient_label": event.get("to") or False,
            "summary": event.get("summary") or False,
            "body": event.get("body") or False,
        }

    def _on_send(self, session, event):
        found = self.search([("sender_session_id", "=", session.id),
                             ("send_tool_use_id", "=", event["tool_use_id"])], limit=1)
        if found:
            return found
        return self.create(self._sender_values(session, event))

    def _on_sent(self, session, event):
        """The answer to a send names its message id: join the two ends."""
        mine = self.search([("sender_session_id", "=", session.id),
                            ("send_tool_use_id", "=", event["tool_use_id"])], limit=1)
        if not mine:
            # The call was read before this model existed, or the line is torn.
            mine = self.create({"sender_session_id": session.id,
                                "send_tool_use_id": event["tool_use_id"],
                                "sent_at": _stamp(event.get("ts"))})
        if mine.msg_id:
            return mine
        other = self.search([("msg_id", "=", event["msg_id"])], limit=1)
        if not other:
            mine.msg_id = event["msg_id"]
            return mine
        take = not other.sender_session_id or self._first(session, other.sender_session_id) == session
        vals = {key: mine[key] for key in (
            "send_tool_use_id", "send_uuid", "sent_at", "recipient_label", "summary", "body")}
        # Either merged into the row the receiving end made, or a fork's copy
        # of a send another session already holds. Gone before the write: the
        # two would share the send's key for a moment.
        mine.unlink()
        if take:
            other.write(dict(vals, sender_session_id=session.id))
        return other

    def _on_answered(self, session, event):
        """A send answered without a message id never left the session.

        Refused, it stays, with the reason. Otherwise it was a message to one of
        the session's own subagents, which is not a message between sessions.
        """
        mine = self.search([("sender_session_id", "=", session.id),
                            ("send_tool_use_id", "=", event["tool_use_id"]),
                            ("msg_id", "=", False)], limit=1)
        if not mine:
            return mine
        if event.get("error"):
            mine.send_error = (event.get("text") or "error")[:500]
            return mine
        mine.unlink()
        return self.browse()

    def _on_received(self, session, event):
        vals = {
            "msg_id": event["msg_id"],
            "recipient_session_id": session.id,
            "receive_uuid": event.get("uuid") or False,
            "received_at": _stamp(event.get("ts")),
            "sender_name": event.get("name") or False,
            "sender_socket": event.get("from") or False,
            "delivery": event.get("mode") or False,
        }
        found = self.search([("msg_id", "=", event["msg_id"])], limit=1)
        if not found:
            return self.create(vals)
        if not found.recipient_session_id \
                or self._first(session, found.recipient_session_id) == session:
            found.write(vals)
        return found

    @api.model
    def _first(self, one, other):
        """Of two sessions holding the same line, the one it was written in.

        A fork copies its parent's lines, so the original is the file that
        starts first; with no start known, the session found first.
        """
        if one == other:
            return one
        Scan = self.env["flightdeck.session.message.scan"]
        starts = {c.session_ref_id.id: c.first_ts
                  for c in Scan.search([("session_ref_id", "in", [one.id, other.id])])}

        def rank(session):
            start = starts.get(session.id)
            return (fields.Datetime.to_string(start) if start else NEVER, session.id)

        return min(one, other, key=rank)

    def _resolve_from_roster(self):
        """Name the other end from the live roster when its file was never read."""
        missing = self.filtered(lambda m: not m.sender_session_id or not m.recipient_session_id)
        if not missing:
            return
        sockets, names = read_peers()
        if not sockets and not names:
            return
        Session = self.env["flightdeck.session"]

        def lookup(label):
            label = label or ""
            if label.startswith(SOCKET_PREFIX):
                found = sockets.get(label[len(SOCKET_PREFIX):])
            else:
                ids = names.get(label) or []
                found = ids[0] if len(ids) == 1 else None
            return Session.search([("session_id", "=", found)], limit=1) if found else Session

        for message in missing:
            if not message.sender_session_id and message.sender_socket:
                message.sender_session_id = lookup(message.sender_socket)
            if not message.recipient_session_id and message.recipient_label:
                message.recipient_session_id = lookup(message.recipient_label)

    # --- conversations ------------------------------------------------------
    def _thread(self):
        """Put each message in the conversation of its two sessions."""
        Thread = self.env["flightdeck.session.thread"]
        threads = Thread
        for message in self:
            a, b = message.sender_session_id, message.recipient_session_id
            if not a or not b or a == b:
                continue
            low, high = (a, b) if a.id < b.id else (b, a)
            thread = Thread.search([("session_a_id", "=", low.id), ("session_b_id", "=", high.id)],
                                   limit=1) or Thread.create(
                {"session_a_id": low.id, "session_b_id": high.id})
            if message.thread_id != thread:
                threads |= message.thread_id
                message.thread_id = thread
            threads |= thread
        threads.exists()._resequence()


class FlightdeckSessionThread(models.Model):
    """The messages two sessions sent each other, in the order they left."""

    _name = "flightdeck.session.thread"
    _description = "Session conversation"
    _order = "last_at desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    session_a_id = fields.Many2one("flightdeck.session", "Session", required=True, index=True,
                                   ondelete="cascade", readonly=True)
    session_b_id = fields.Many2one("flightdeck.session", "Other session", required=True,
                                   index=True, ondelete="cascade", readonly=True)
    message_ids = fields.One2many("flightdeck.session.message", "thread_id", "Conversation messages")
    message_count = fields.Integer("Messages", readonly=True)
    reply_count = fields.Integer("Replies", readonly=True,
                                 help="Messages that answer one the other session sent.")
    exchange_count = fields.Integer(
        "Exchanges", readonly=True,
        help="Runs of messages from one side; each change of sender starts a new one.")
    avg_reply_minutes = fields.Float("Average reply (min)", digits=(10, 1), readonly=True)
    first_at = fields.Datetime("First", readonly=True)
    last_at = fields.Datetime("Last", index=True, readonly=True)
    duration_hours = fields.Float("Hours", digits=(10, 2), readonly=True)

    _pair_uniq = models.Constraint("unique(session_a_id, session_b_id)",
                                   "These two sessions already have a conversation.")

    @api.depends("session_a_id.name", "session_b_id.name")
    def _compute_name(self):
        for rec in self:
            rec.name = "%s ⇄ %s" % (rec.session_a_id.name or "?", rec.session_b_id.name or "?")

    def _resequence(self):
        """Order, reply links and totals, worked out again from the messages."""
        for thread in self:
            messages = thread.message_ids.sorted(
                lambda m: (m.sent_at or m.received_at or fields.Datetime.now(), m.id))
            last_from = {}
            replies, waits, exchanges, sender = 0, [], 0, None
            for number, message in enumerate(messages, 1):
                answered = last_from.get(message.recipient_session_id.id)
                vals = {"sequence": number, "reply_to_id": answered.id if answered else False,
                        "reply_minutes": 0.0, "delivery_seconds": 0.0}
                arrived = answered and (answered.received_at or answered.sent_at)
                if arrived and message.sent_at:
                    vals["reply_minutes"] = max(0.0, (message.sent_at - arrived).total_seconds() / 60)
                    waits.append(vals["reply_minutes"])
                if message.sent_at and message.received_at:
                    vals["delivery_seconds"] = max(
                        0.0, (message.received_at - message.sent_at).total_seconds())
                if answered:
                    replies += 1
                if message.sender_session_id != sender:
                    exchanges += 1
                    sender = message.sender_session_id
                message.write(vals)
                last_from[message.sender_session_id.id] = message
                # A message answers the latest one from the other side, once.
                last_from.pop(message.recipient_session_id.id, None)
            stamps = [m.sent_at or m.received_at for m in messages if m.sent_at or m.received_at]
            thread.write({
                "message_count": len(messages),
                "reply_count": replies,
                "exchange_count": exchanges,
                "avg_reply_minutes": sum(waits) / len(waits) if waits else 0.0,
                "first_at": min(stamps) if stamps else False,
                "last_at": max(stamps) if stamps else False,
                "duration_hours": (max(stamps) - min(stamps)).total_seconds() / 3600 if stamps else 0.0,
            })

    def action_open_messages(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "flight_deck.action_session_message")
        action.update({"domain": [("thread_id", "=", self.id)],
                       "context": {"default_thread_id": self.id}, "name": self.name})
        return action


class FlightdeckSessionPeers(models.Model):
    _inherit = "flightdeck.session"

    peer_message_count = fields.Integer("Session messages", compute="_compute_peer_message_count")

    def _compute_peer_message_count(self):
        Message = self.env["flightdeck.session.message"]
        for session in self:
            session.peer_message_count = Message.search_count([
                "|", ("sender_session_id", "=", session.id),
                ("recipient_session_id", "=", session.id)]) if session.id else 0

    def action_open_session_messages(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "flight_deck.action_session_message")
        action.update({
            "domain": ["|", ("sender_session_id", "=", self.id), ("recipient_session_id", "=", self.id)],
            "name": "Session messages: %s" % self.name,
        })
        return action
