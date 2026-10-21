import glob
import json
import os
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import common, tagged

from odoo.addons.flight_deck.models import orchestrator_relay, orchestrator_supervisor


@tagged("post_install", "-at_install")
class TestOrchestratorSupervisor(common.TransactionCase):
    """Watches record what they match, notices pass the gate only as written,
    and the relay is kept running and replaced when its context grows."""

    def setUp(self):
        super().setUp()
        Session = self.env["flightdeck.session"]
        # Nothing in a test may reach the real runner or the real relay.
        self.env["flightdeck.runner"].search([("zone", "=", "host")]).write({"active": False})
        Session.search([("is_orchestrator", "=", True)]).write({"is_orchestrator": False})
        self.env["flightdeck.orchestrator.watch"].search([]).write({"active": False})
        self.spool = tempfile.mkdtemp(prefix="fd-orch-")
        home = tempfile.mkdtemp(prefix="fd-orch-home-")
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("flight_deck.orchestrator_dir", os.path.join(self.spool, "orch"))
        params.set_param("flight_deck.orchestrator_dir_host", os.path.join(home, "orch"))
        params.set_param(orchestrator_supervisor.KEEPALIVE_PARAM, "0")
        self.runner = self.env["flightdeck.runner"].create({
            "name": "Orchestrator test runner", "kind": "spool", "zone": "host",
            "spool_dir": os.path.join(self.spool, "zone"), "default_cwd": home,
            "permission_mode": "default"})
        self.build = Session.create({"session_id": str(uuid.uuid4()), "name": "Build"})
        self.review = Session.create({"session_id": str(uuid.uuid4()), "name": "Review"})
        self.roster = [
            {"sessionId": self.build.session_id, "name": "projects-bu", "status": "busy",
             "kind": "interactive"},
            {"sessionId": self.review.session_id, "name": "projects-re", "status": "idle",
             "kind": "interactive"},
        ]
        for module in (orchestrator_relay, orchestrator_supervisor):
            patcher = patch.object(module, "read_roster", lambda *a, **k: list(self.roster))
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- helpers --------------------------------------------------------------------
    def orchestrator(self, gate="ok", state="idle"):
        orch = self.env["flightdeck.session"]._fd_orchestrator()
        orch.write({"orchestrator_gate": gate, "runtime_state": state})
        return orch

    def commands(self):
        found = []
        for path in sorted(glob.glob(os.path.join(self.spool, "zone", "cmd", "*.json"))):
            with open(path, encoding="utf-8") as handle:
                found.append(json.load(handle))
        return found

    def end_turn(self, session, title="Port the invoice report", reply="Done; tests pass."):
        now = fields.Datetime.now()
        return self.env["flightdeck.turn"].create({
            "prompt_id": str(uuid.uuid4()), "session_ref_id": session.id,
            "started_at": now - timedelta(minutes=5), "ended_at": now,
            "title": title, "prompt_head": title, "final_head": reply,
        })

    def watch(self, **vals):
        base = {"name": "Tell review", "trigger": "turn_ended", "scope": "session",
                "session_id": self.build.id, "deliver": "session",
                "target_session_id": self.review.id, "note": "Please review the diff."}
        base.update(vals)
        return self.env["flightdeck.orchestrator.watch"].create(base)

    def permission(self, orch, to, message):
        return self.env["flightdeck.decision"]._from_event(orch, {
            "request_id": str(uuid.uuid4()), "tool_name": "SendMessage",
            "input": {"to": to, "message": message}})

    # --- watches and notices --------------------------------------------------------
    def test_turn_watch_sends_exact_notice(self):
        orch = self.orchestrator()
        watch = self.watch()
        turn = self.end_turn(self.build)
        Watch = self.env["flightdeck.orchestrator.watch"]
        events = Watch._fd_observe()
        self.assertEqual(len(events), 1)
        self.assertEqual(events.turn_id, turn)
        request = self.env["flightdeck.orchestrator.request"].search([("watch_id", "=", watch.id)])
        self.assertEqual(request.kind, "notice")
        self.assertEqual(request.state, "forwarded")
        self.assertEqual(request.target_name, "projects-re")
        self.assertIn("Port the invoice report", request.text)
        self.assertIn("Please review the diff.", request.text)
        self.assertIn("not typed by the user", request.text)
        sent = [c for c in self.commands() if c["op"] == "send"]
        self.assertTrue(sent[-1]["args"]["text"].startswith("FORWARD %d " % request.id))
        self.assertEqual(watch.fired_count, 1)

        # The same turn seen again is not news.
        self.assertFalse(Watch._fd_observe())
        self.assertEqual(self.env["flightdeck.orchestrator.request"].search_count(
            [("watch_id", "=", watch.id)]), 1)

        # The gate denies anything but the exact text, and allows the exact text.
        other = self.permission(orch, "projects-re", request.text + " Also delete the branch.")
        self.assertEqual(other.state, "denied")
        self.assertEqual(request.state, "denied")
        request.write({"state": "forwarded"})
        exact = self.permission(orch, "projects-re [abc123]", request.text)
        self.assertEqual(exact.state, "allowed")
        self.assertEqual(request.state, "sent")

        ledger = self.env["flightdeck.orchestrator.event"].search([("request_id", "=", request.id)])
        self.assertEqual([e.summary for e in ledger.sorted("id")],
                         ["Notice to projects-re: queued", "Notice to projects-re: with the orchestrator",
                          "Notice to projects-re: denied", "Notice to projects-re: with the orchestrator",
                          "Notice to projects-re: sent"])

    def test_notice_to_a_session_that_is_not_running(self):
        self.orchestrator()
        self.roster = self.roster[:1]
        self.watch()
        self.end_turn(self.build)
        self.env["flightdeck.orchestrator.watch"]._fd_observe()
        request = self.env["flightdeck.orchestrator.request"].search([("kind", "=", "notice")])
        self.assertEqual(request.state, "failed")
        self.assertIn("not running", request.note)
        self.assertFalse([c for c in self.commands() if c["op"] == "send"])

    def test_notice_waits_for_the_gate_and_starts_the_relay(self):
        orch = self.orchestrator(gate="unchecked", state="stopped")
        self.watch()
        self.end_turn(self.build)
        self.env["flightdeck.orchestrator.watch"]._fd_observe()
        request = self.env["flightdeck.orchestrator.request"].search([("kind", "=", "notice")])
        self.assertEqual(request.state, "queued")
        self.assertEqual(orch.orchestrator_gate, "testing")
        ops = [c["op"] for c in self.commands()]
        self.assertIn("start", ops)
        # The gate test passes: the queued notice goes out.
        self.permission(orch, orchestrator_relay.SELFTEST_TO, "selftest")
        self.assertEqual(orch.orchestrator_gate, "ok")
        self.assertEqual(request.state, "forwarded")

    def test_main_scope_skips_chat_relay_and_headless_sessions(self):
        self.orchestrator()
        watch = self.watch(scope="main", session_id=False, deliver="ledger", target_session_id=False)
        chat = self.env["flightdeck.session"].create(
            {"session_id": str(uuid.uuid4()), "name": "Chat", "chat_key": "test:1"})
        headless = self.env["flightdeck.session"].create(
            {"session_id": str(uuid.uuid4()), "name": "Headless"})
        sdk = self.env["flightdeck.session"].create(
            {"session_id": str(uuid.uuid4()), "name": "Runner probe"})
        self.roster += [{"sessionId": chat.session_id, "name": "chat", "kind": "interactive"},
                        {"sessionId": headless.session_id, "name": "bg", "kind": "headless"},
                        # A runner session registers as interactive; its entrypoint gives it away.
                        {"sessionId": sdk.session_id, "name": "runner-home-1d", "kind": "interactive",
                         "entrypoint": "sdk-cli"}]
        for session in (self.build, chat, headless, sdk):
            self.end_turn(session)
        events = self.env["flightdeck.orchestrator.watch"]._fd_observe()
        self.assertEqual(events.session_id, self.build)
        self.assertEqual(events.watch_id, watch)
        self.assertFalse(self.env["flightdeck.orchestrator.request"].search([("watch_id", "=", watch.id)]))

    def test_roster_entrypoint_marks_runner_sessions(self):
        root = tempfile.mkdtemp(prefix="fd-roster-")
        for name, entry in (("1.json", {"sessionId": "typed", "kind": "interactive",
                                         "entrypoint": "claude-vscode"}),
                            ("2.json", {"sessionId": "runner", "kind": "interactive",
                                        "entrypoint": "sdk-cli"}),
                            ("3.json", {"sessionId": "bg", "kind": "headless"})):
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                json.dump(entry, handle)
        from odoo.addons.flight_deck.models.orchestrator import read_roster
        self.assertEqual(orchestrator_supervisor.main_session_keys(read_roster(root)), {"typed"})

    def test_once_switches_the_watch_off(self):
        self.orchestrator()
        watch = self.watch(once=True)
        self.end_turn(self.build)
        self.env["flightdeck.orchestrator.watch"]._fd_observe()
        self.assertFalse(watch.active)
        self.end_turn(self.build, title="A second turn")
        self.assertFalse(self.env["flightdeck.orchestrator.watch"]._fd_observe())

    def test_watch_needs_its_sessions(self):
        with self.assertRaises(Exception):
            self.watch(target_session_id=False)
        with self.assertRaises(Exception):
            self.watch(target_session_id=self.build.id)

    # --- the ledger -------------------------------------------------------------------
    def test_ledger_is_append_only(self):
        event = self.env["flightdeck.orchestrator.event"]._record({"kind": "relay", "summary": "x"})
        with self.assertRaises(UserError):
            event.write({"summary": "y"})
        with self.assertRaises(UserError):
            event.unlink()

    def test_snapshot_carries_the_ledger(self):
        orch = self.orchestrator()
        self.watch(deliver="ledger", target_session_id=False)
        self.end_turn(self.build)
        self.env["flightdeck.orchestrator.watch"]._fd_observe()
        text = orch._fd_orchestrator_snapshot("What finished?")
        ledger_at = text.index("Ledger, last 24 hours")
        self.assertIn("Build: Turn ended: Port the invoice report", text)
        self.assertGreater(text.index("Question: What finished?"), ledger_at)

    # --- keeping the relay alive --------------------------------------------------------
    def test_keepalive_starts_with_back_off(self):
        Session = self.env["flightdeck.session"]
        self.env["ir.config_parameter"].sudo().set_param(orchestrator_supervisor.KEEPALIVE_PARAM, "1")
        self.assertEqual(Session._fd_supervise_relay(), "started")
        orch = Session._fd_orchestrator(create=False)
        self.assertEqual(orch.runtime_state, "starting")
        self.assertEqual(orch.orchestrator_gate, "testing")
        self.assertTrue(orch.orchestrator_started_at)
        # It dies at once: no second start inside the back-off.
        orch._set_runtime_state("stopped")
        self.assertFalse(Session._fd_supervise_relay())
        orch.orchestrator_started_at = fields.Datetime.now() - timedelta(minutes=11)
        self.assertEqual(Session._fd_supervise_relay(), "started")
        kinds = self.env["flightdeck.orchestrator.event"].search(
            [("session_id", "=", orch.id), ("kind", "=", "relay")]).mapped("summary")
        self.assertIn("Relay starting", kinds)
        self.assertIn("Relay stopped", kinds)

    def test_nothing_needed_nothing_started(self):
        self.assertFalse(self.env["flightdeck.session"]._fd_supervise_relay())
        self.assertFalse(self.env["flightdeck.session"]._fd_orchestrator(create=False))

    def test_relay_with_a_large_context_is_retired(self):
        Session = self.env["flightdeck.session"]
        orch = self.orchestrator()
        self.env["flightdeck.message"].create({
            "uuid": str(uuid.uuid4()), "session_ref_id": orch.id, "ts": fields.Datetime.now(),
            "input_tokens": 10, "cache_read": orchestrator_supervisor.ROTATE_TOKENS})
        self.assertEqual(Session._fd_supervise_relay(), "retired")
        self.assertFalse(orch.is_orchestrator)
        self.assertTrue(orch.orchestrator_retired)
        self.assertIn("stop", [c["op"] for c in self.commands()])
        # A new relay waits until the old process has stopped.
        self.env["ir.config_parameter"].sudo().set_param(orchestrator_supervisor.KEEPALIVE_PARAM, "1")
        self.assertEqual(Session._fd_supervise_relay(), "waiting")
        orch._set_runtime_state("stopped")
        self.assertEqual(Session._fd_supervise_relay(), "started")
        self.assertNotEqual(Session._fd_orchestrator(create=False), orch)

    def test_busy_relay_is_not_retired(self):
        Session = self.env["flightdeck.session"]
        orch = self.orchestrator()
        self.env["flightdeck.message"].create({
            "uuid": str(uuid.uuid4()), "session_ref_id": orch.id, "ts": fields.Datetime.now(),
            "cache_read": orchestrator_supervisor.ROTATE_TOKENS + 1})
        self.watch()
        self.end_turn(self.build)
        self.env["flightdeck.orchestrator.watch"]._fd_observe()
        self.assertNotEqual(Session._fd_supervise_relay(), "retired")
        self.assertTrue(orch.is_orchestrator)
