"""Supervise sessions from the Orchestrator: watch rules, a ledger, a relay kept alive.

A watch names what to look for (a turn that ended, an approval waited on, an
agent that failed), in which sessions, and where to deliver it: the ledger
only, a web push, or a notice to another session. The follow cron runs the
watches after it has read the transcripts, so a turn is seen within the same
pass that stored it.

A notice to a session goes through the relay (`orchestrator_relay.py`) as a
request of kind `notice`. Flightdoo writes its text from the watch, never the
model, and the relay's gate allows it only when the SendMessage call carries
that text exactly, as it does for a message a person confirmed.

The ledger (`flightdeck.orchestrator.event`) keeps what the supervisor saw
and did: every observation a watch matched, every relay request and how it
ended, every start, stop, gate test and replacement of the relay process. It
is append-only. The relay's own context holds nothing the supervisor needs:
each request is self-contained and a question is answered from a snapshot that
includes the recent ledger, so the process can be replaced at any time.

Keeping the relay alive: while a watch delivers to sessions, or "keep running"
is on, a stopped relay is started again, at most once per back-off period.
When its context grows past a limit it is retired and a fresh one takes the
name, because a long-lived Haiku context costs more per turn and helps nothing.
"""
import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .orchestrator import read_roster

_logger = logging.getLogger(__name__)

TRIGGERS = [
    ("turn_ended", "A turn ended"),
    ("approval", "Waiting for approval"),
    ("agent_failed", "An agent failed"),
]
SCOPES = [("main", "Every main session"), ("session", "One session")]
DELIVERIES = [
    ("ledger", "Ledger only"),
    ("push", "Push to me"),
    ("session", "Notice to a session"),
]
EVENT_KINDS = TRIGGERS + [
    ("request", "Relay request"),
    ("relay", "Relay"),
]
# How far back an observation may lie. Older ones are history, not news, and
# the dedupe key covers the overlap between two passes.
SCAN_WINDOW = timedelta(minutes=15)
# The follow cron can run several times a second while files grow.
SUPERVISE_EVERY = 20.0
RESTART_BACKOFF = timedelta(minutes=10)
ROTATE_TOKENS = 80_000
KEEPALIVE_PARAM = "flight_deck.orchestrator_keepalive"
NOTICE_HEAD = 600

_last_supervise = {}


def main_session_keys(roster):
    """The sessions a person works in: interactive, and not driven over the SDK.

    A runner session and `claude -p` also register as `interactive`; only their
    entrypoint (`sdk-cli`, `sdk-ts`, ...) tells them apart.
    """
    return {r["sessionId"] for r in roster
            if (r.get("kind") or "interactive") == "interactive"
            and not (r.get("entrypoint") or "").startswith("sdk")}


class FlightdeckOrchestratorEvent(models.Model):
    _name = "flightdeck.orchestrator.event"
    _description = "Orchestrator ledger entry"
    _order = "id desc"
    _rec_name = "summary"

    at = fields.Datetime(default=fields.Datetime.now, required=True, index=True, readonly=True)
    kind = fields.Selection(EVENT_KINDS, required=True, index=True, readonly=True)
    session_id = fields.Many2one("flightdeck.session", "Session", ondelete="set null", index=True,
                                 readonly=True)
    watch_id = fields.Many2one("flightdeck.orchestrator.watch", ondelete="set null", index=True,
                               readonly=True)
    request_id = fields.Many2one("flightdeck.orchestrator.request", ondelete="set null",
                                 readonly=True)
    turn_id = fields.Many2one("flightdeck.turn", ondelete="set null", readonly=True)
    agent_id = fields.Many2one("flightdeck.session.agent", ondelete="set null", readonly=True)
    decision_id = fields.Many2one("flightdeck.decision", ondelete="set null", readonly=True)
    summary = fields.Char(required=True, readonly=True)
    detail = fields.Text(readonly=True)
    key = fields.Char(readonly=True, help="Names an observation, so a second pass does not record it again.")

    _key_uniq = models.UniqueIndex("(key) WHERE key IS NOT NULL",
                                   "This observation is already in the ledger.")

    def write(self, vals):
        raise UserError(_("The ledger is append-only."))

    def unlink(self):
        if not self.env.context.get("fd_ledger_prune"):
            raise UserError(_("The ledger is append-only."))
        return super().unlink()

    @api.model
    def _record(self, vals):
        return self.sudo().create(vals)

    def _row(self):
        self.ensure_one()
        return {
            "id": self.id,
            "at": fields.Datetime.to_string(self.at),
            "kind": self.kind,
            "kind_label": dict(EVENT_KINDS)[self.kind],
            "session": self.session_id.name or "",
            "summary": self.summary,
        }


class FlightdeckOrchestratorWatch(models.Model):
    _name = "flightdeck.orchestrator.watch"
    _description = "Orchestrator watch"
    _order = "sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    trigger = fields.Selection(TRIGGERS, required=True, default="turn_ended")
    scope = fields.Selection(SCOPES, required=True, default="session",
                             help="Every main session: the interactive sessions on this machine, "
                                  "not the relay, chat sessions or headless runs.")
    session_id = fields.Many2one("flightdeck.session", "Watched session", ondelete="cascade",
                                 domain=[("is_orchestrator", "=", False)])
    deliver = fields.Selection(DELIVERIES, required=True, default="ledger")
    target_session_id = fields.Many2one("flightdeck.session", "Tell session", ondelete="cascade",
                                        domain=[("is_orchestrator", "=", False)])
    note = fields.Text(help="Added under the notice, e.g. what the other session should do next.")
    once = fields.Boolean("Only the next time",
                          help="Switch the watch off after it fires once.")
    fired_count = fields.Integer(readonly=True)
    last_fired_at = fields.Datetime(readonly=True)
    event_ids = fields.One2many("flightdeck.orchestrator.event", "watch_id")

    @api.constrains("scope", "session_id", "deliver", "target_session_id")
    def _check_targets(self):
        for watch in self:
            if watch.scope == "session" and not watch.session_id:
                raise ValidationError(_("Pick the session to watch."))
            if watch.deliver == "session" and not watch.target_session_id:
                raise ValidationError(_("Pick the session to tell."))
            if watch.deliver == "session" and watch.scope == "session" \
                    and watch.target_session_id == watch.session_id:
                raise ValidationError(_("A session cannot be told about itself."))

    def _matches(self, session, main_keys):
        self.ensure_one()
        if not session or session.is_orchestrator:
            return False
        if self.scope == "session":
            return session == self.session_id
        return session.session_id in main_keys and not session.chat_key and not session.runner_id

    # --- the pass ---------------------------------------------------------------
    @api.model
    def _fd_supervise(self, force=False):
        """One supervisor pass: observe, deliver, keep the relay alive."""
        now_s = time.monotonic()
        db = self.env.cr.dbname
        if not force and now_s - _last_supervise.get(db, -SUPERVISE_EVERY) < SUPERVISE_EVERY:
            return False
        _last_supervise[db] = now_s
        self = self.sudo()
        self._fd_observe()
        self.env["flightdeck.session"].sudo()._fd_supervise_relay()
        return True

    @api.model
    def _fd_observe(self):
        watches = self.search([])
        if not watches:
            return self.env["flightdeck.orchestrator.event"]
        since = fields.Datetime.now() - SCAN_WINDOW
        found = []
        triggers = set(watches.mapped("trigger"))
        if "turn_ended" in triggers:
            for turn in self.env["flightdeck.turn"].search([("ended_at", ">=", since)], order="ended_at, id"):
                found.append(("turn_ended", "turn:%d" % turn.id, turn.session_ref_id, {
                    "turn_id": turn.id,
                    # display_name falls back to a title made from the prompt, which
                    # turns a slash command's markup into its name.
                    "summary": "Turn ended: %s" % ((turn.display_name or "").strip()[:150]
                                                   or _("(no prompt)")),
                    "detail": turn.final_head or "",
                }))
        if "approval" in triggers:
            for decision in self.env["flightdeck.decision"].search(
                    [("create_date", ">=", since), ("state", "=", "pending")], order="id"):
                found.append(("approval", "decision:%d" % decision.id, decision.session_ref_id, {
                    "decision_id": decision.id,
                    "summary": "Waiting for approval: %s" % (decision.tool_name or ""),
                    "detail": decision.summary or "",
                }))
        if "agent_failed" in triggers:
            for agent in self.env["flightdeck.session.agent"].search(
                    [("status", "=", "failed"), ("write_date", ">=", since)], order="id"):
                found.append(("agent_failed", "agent:%d" % agent.id, agent.session_ref_id, {
                    "agent_id": agent.id,
                    "summary": "Agent failed: %s" % (agent.description or agent.agent_type or "agent"),
                    "detail": agent.summary or "",
                }))
        if not found:
            return self.env["flightdeck.orchestrator.event"]
        main_keys = main_session_keys(read_roster())
        Event = self.env["flightdeck.orchestrator.event"]
        candidates = [(kind, "%s:w%d" % (key, w.id), session, vals, w)
                      for kind, key, session, vals in found
                      for w in watches if w.trigger == kind and w._matches(session, main_keys)]
        seen = set(Event.search([("key", "in", [c[1] for c in candidates])]).mapped("key"))
        made = Event
        for kind, key, session, vals, watch in candidates:
            if key in seen or not watch.active:
                continue
            event = Event._record(dict(vals, kind=kind, key=key, session_id=session.id,
                                       watch_id=watch.id))
            made |= event
            watch._fd_deliver(event)
        return made

    def _fd_deliver(self, event):
        self.ensure_one()
        if self.deliver == "push":
            event.session_id._push_to_owners(
                "%s: %s" % (self.name, event.session_id.display_name), event.summary,
                "flightdeck.session", event.session_id.id, "fd-watch-%d" % event.id)
        elif self.deliver == "session":
            self.env["flightdeck.session"]._fd_orchestrator_notice(
                self.target_session_id, self._fd_notice_text(event), self, event)
        vals = {"fired_count": self.fired_count + 1, "last_fired_at": fields.Datetime.now()}
        if self.once:
            vals["active"] = False
        self.write(vals)

    def _fd_notice_text(self, event):
        self.ensure_one()
        head = " ".join((event.detail or "").split())
        if len(head) > NOTICE_HEAD:
            head = head[:NOTICE_HEAD] + "…"
        lines = ["[Flightdoo watch \"%s\"] %s — %s" % (
            self.name, event.session_id.name or "a session", event.summary)]
        if head:
            lines.append("Last words: %s" % head)
        if self.note:
            lines.append(self.note.strip())
        lines.append("This notice was written by Flightdoo from a watch rule, not typed by the user.")
        return "\n".join(lines)


class FlightdeckOrchestratorRequestLedger(models.Model):
    _inherit = "flightdeck.orchestrator.request"

    watch_id = fields.Many2one("flightdeck.orchestrator.watch", ondelete="set null", readonly=True)

    def _fd_ledger_line(self, what):
        label = dict(self._fields["kind"].selection)[self.kind]
        if self.kind == "ask":
            head = "Question"
        elif self.kind == "selftest":
            head = "Gate test"
        else:
            head = "%s to %s" % (label, self.target_name or "?")
        return "%s: %s" % (head, what)

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        Event = self.env["flightdeck.orchestrator.event"]
        for request in requests:
            Event._record({
                "kind": "request", "request_id": request.id, "watch_id": request.watch_id.id,
                "session_id": request.target_session_id.id,
                "summary": request._fd_ledger_line(_("queued")),
                "detail": request.text,
            })
        return requests

    def write(self, vals):
        before = {r.id: r.state for r in self} if "state" in vals else {}
        res = super().write(vals)
        if before:
            Event = self.env["flightdeck.orchestrator.event"]
            states = dict(self._fields["state"].selection)
            for request in self.filtered(lambda r: before.get(r.id) != r.state):
                Event._record({
                    "kind": "request", "request_id": request.id, "watch_id": request.watch_id.id,
                    "session_id": request.target_session_id.id,
                    "summary": request._fd_ledger_line(states[request.state].lower()),
                    "detail": "\n".join(filter(None, [request.note, request.reply])) or False,
                })
        return res


class FlightdeckSessionSupervisor(models.Model):
    _inherit = "flightdeck.session"

    orchestrator_started_at = fields.Datetime(
        readonly=True, help="When Flightdoo last started this relay process.")
    orchestrator_retired = fields.Boolean(
        readonly=True, help="A relay replaced because its context grew; kept for its logbook.")

    # --- notices -------------------------------------------------------------------
    @api.model
    def _fd_orchestrator_notice(self, target, text, watch, event):
        """Queue a notice Flightdoo wrote for a live session, as the relay's gate expects."""
        orch = self._fd_orchestrator()
        entry = next((r for r in read_roster() if r["sessionId"] == target.session_id), None)
        Request = self.env["flightdeck.orchestrator.request"]
        request = Request.create({
            "kind": "notice", "orchestrator_id": orch.id, "target_session_id": target.id,
            "target_name": (entry or {}).get("name") or target.name, "text": text,
            "watch_id": watch.id,
        })
        if not entry or not entry.get("name"):
            request.write({"state": "failed", "note": "%s is not running." % target.name})
            return request
        if orch.orchestrator_gate == "ok" and orch.runtime_state != "stopped":
            request._fd_forward()
        else:
            self._fd_supervise_relay(need=True)
        return request

    # --- the relay's life ----------------------------------------------------------
    @api.model
    def _fd_orchestrator_check_user(self):
        # The supervisor runs from the follow cron; a person still needs the group.
        if self.env.su:
            return
        return super()._fd_orchestrator_check_user()

    @api.model
    def fd_orchestrator_start(self):
        result = super().fd_orchestrator_start()
        orch = self._fd_orchestrator(create=False)
        if orch and orch.orchestrator_gate == "testing":
            orch.sudo().orchestrator_started_at = fields.Datetime.now()
        return result

    @api.model
    def _fd_relay_needed(self):
        params = self.env["ir.config_parameter"].sudo()
        if params.get_param(KEEPALIVE_PARAM) in ("1", "True", "true"):
            return True
        if self.env["flightdeck.orchestrator.watch"].sudo().search_count(
                [("deliver", "=", "session")], limit=1):
            return True
        return bool(self.env["flightdeck.orchestrator.request"].sudo().search_count(
            [("state", "=", "queued"), ("kind", "in", ("send", "notice"))], limit=1))

    @api.model
    def _fd_supervise_relay(self, need=None):
        """Start a stopped relay that is needed, replace one whose context grew."""
        need = self._fd_relay_needed() if need is None else need
        if self.search_count([("orchestrator_retired", "=", True), ("runtime_state", "!=", "stopped"),
                              ("write_date", ">", fields.Datetime.now() - RESTART_BACKOFF)]):
            # Two processes would both take the name fd-orchestrator. A stop the
            # runner never confirmed stops blocking after the back-off.
            return "waiting"
        orch = self._fd_orchestrator(create=need)
        if not orch:
            return False
        if orch.runtime_state != "stopped":
            orch._fd_orchestrator_catch_up()
        busy = self.env["flightdeck.orchestrator.request"].search_count(
            [("orchestrator_id", "=", orch.id), ("state", "in", ("queued", "forwarded"))])
        if orch.runtime_state != "stopped" and not busy \
                and orch._fd_context_tokens() > ROTATE_TOKENS:
            orch._fd_retire()
            return "retired"
        if not need:
            return False
        recent = orch.orchestrator_started_at and \
            orch.orchestrator_started_at > fields.Datetime.now() - RESTART_BACKOFF
        if orch.orchestrator_gate == "broken" and orch.runtime_state != "stopped":
            if not recent:
                orch.action_stop_runtime()
                return "stopping"
            return False
        if orch.runtime_state == "stopped" and not recent:
            self.fd_orchestrator_start()
            return "started"
        return False

    def _fd_context_tokens(self):
        """How much context the relay's last call carried."""
        self.ensure_one()
        msg = self.env["flightdeck.message"].search(
            [("session_ref_id", "=", self.id), ("agent_key", "in", ["", False])],
            order="ts desc, id desc", limit=1)
        if not msg:
            return 0
        return int((msg.input_tokens or 0) + (msg.cache_read or 0)
                   + (msg.cache_create_5m or 0) + (msg.cache_create_1h or 0))

    def _fd_retire(self):
        """Stop this relay and hand the name to a fresh one at the next pass."""
        self.ensure_one()
        tokens = self._fd_context_tokens()
        self.action_stop_runtime()
        self.write({"is_orchestrator": False, "orchestrator_retired": True,
                    "name": "Orchestrator (retired %s)" % fields.Date.today()})
        self.env["flightdeck.orchestrator.event"]._record({
            "kind": "relay", "session_id": self.id,
            "summary": "Relay retired: its context reached %s tokens." % tokens,
        })

    def write(self, vals):
        before = {s.id: s.orchestrator_gate for s in self} if "orchestrator_gate" in vals else {}
        res = super().write(vals)
        if before:
            gates = dict(self._fields["orchestrator_gate"].selection)
            for session in self.filtered(lambda s: s.is_orchestrator and before.get(s.id) != s.orchestrator_gate):
                self.env["flightdeck.orchestrator.event"]._record({
                    "kind": "relay", "session_id": session.id,
                    "summary": "Send gate: %s" % gates[session.orchestrator_gate].lower(),
                })
        return res

    def _set_runtime_state(self, state, pid=None, error=None):
        changed = self.filtered(lambda s: s.is_orchestrator and s.runtime_state != state
                                and state in ("starting", "stopped"))
        res = super()._set_runtime_state(state, pid=pid, error=error)
        for session in changed:
            self.env["flightdeck.orchestrator.event"]._record({
                "kind": "relay", "session_id": session.id,
                "summary": "Relay starting" if state == "starting" else "Relay stopped",
                "detail": error or False,
            })
        return res

    # --- reading -------------------------------------------------------------------
    def _fd_orchestrator_snapshot(self, question):
        """The page's snapshot, with the recent ledger before the question."""
        text = super()._fd_orchestrator_snapshot(question)
        head, sep, tail = text.rpartition("\nQuestion: ")
        if not sep:
            return text
        since = fields.Datetime.now() - timedelta(hours=24)
        events = self.env["flightdeck.orchestrator.event"].search(
            [("at", ">=", since), ("kind", "!=", "request")], limit=30)
        if not events:
            return text
        lines = ["", "Ledger, last 24 hours, newest first:"]
        lines += ["- %s UTC %s%s" % (fields.Datetime.to_string(e.at)[:16],
                                     "%s: " % e.session_id.name if e.session_id else "", e.summary)
                  for e in events]
        return head + "\n".join(lines) + "\n" + sep + tail

    @api.model
    def fd_orchestrator_tree(self):
        data = super().fd_orchestrator_tree()
        params = self.env["ir.config_parameter"].sudo()
        watches = self.env["flightdeck.orchestrator.watch"].search([], limit=20)
        data["supervisor"] = {
            "keepalive": params.get_param(KEEPALIVE_PARAM) in ("1", "True", "true"),
            "watches": [{
                "id": w.id, "name": w.name,
                "trigger": dict(TRIGGERS)[w.trigger],
                "deliver": dict(DELIVERIES)[w.deliver],
                "target": w.target_session_id.name or "",
                "fired": w.fired_count,
            } for w in watches],
            "ledger": [e._row() for e in self.env["flightdeck.orchestrator.event"].search([], limit=15)],
        }
        return data

    @api.model
    def fd_orchestrator_set_keepalive(self, on):
        self._fd_orchestrator_check_user()
        self.env["ir.config_parameter"].sudo().set_param(KEEPALIVE_PARAM, "1" if on else "0")
        if on:
            self._fd_supervise_relay(need=True)
        return True

    @api.model
    def _cron_follow(self):
        result = super()._cron_follow()
        try:
            with self.env.cr.savepoint():
                self.env["flightdeck.orchestrator.watch"]._fd_supervise()
        except Exception:
            _logger.exception("The orchestrator supervisor pass failed.")
        return result
