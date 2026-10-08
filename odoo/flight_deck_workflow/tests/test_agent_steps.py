import glob
import json
import os
import tempfile

from odoo.exceptions import UserError
from odoo.tests import common, new_test_user, tagged

from odoo.addons.queue_job.tests.common import trap_jobs
from odoo.addons.workflow_studio.models.ir_workflow_orchestration import waiting_types
from odoo.addons.workflow_studio.models.runners.errors import PermanentNodeError
from odoo.addons.workflow_studio.models.runners.safety import atomic_violations

from ..models.agent_runners import AskAgentNodeRunner
from odoo.addons.workflow_studio.tests.test_join import build, code


@tagged("post_install", "-at_install")
class TestAgentSteps(common.TransactionCase):
    """Ask Agent and Start Session: a flow talks to a FlightDeck session through the chat bridge."""

    def setUp(self):
        super().setUp()
        self.env["workflow.run.wait"].search([("state", "in", ("open", "reserved"))]).write({"state": "cancelled"})
        self.spool = tempfile.mkdtemp(prefix="fd-agent-")
        # The bridge takes the first active container runner: this one, alone.
        self.env["flightdeck.runner"].search([("zone", "=", "container")]).write({"active": False})
        self.runner = self.env["flightdeck.runner"].create({
            "name": "Agent step runner", "kind": "spool", "zone": "container", "spool_dir": self.spool,
            "default_cwd": "/tmp/fd-agent-home", "permission_mode": "dontAsk"})

    # --- helpers ---------------------------------------------------------------

    def flow(self, ask_config, after=None, user=None):
        flow = self.env["ir.workflow"].with_user(user or self.env.user).create(
            {"name": "Agent %s" % self._testMethodName})
        nodes = [
            ("start", "schedule_trigger", {}),
            ("input", "code", code("{'chat': 'c-1', 'text': 'What is 2 + 2?'}")),
            ("ask", "fd_ask_agent", ask_config),
            ("done", "code", code(after or "{'said': _json['answer'], 'chat': _json['input']['chat']}")),
            ("late", "code", code("{'late': True, 'chat': _json['input']['chat']}")),
        ]
        wires = [("start", "output", "input", "data"), ("input", "output", "ask", "data"),
                 ("ask", "answer", "done", "data"), ("ask", "timeout", "late", "data")]
        flow.sudo().save_workflow(build(nodes, wires))
        return flow

    def drain(self, trap):
        with self.enter_registry_test_mode():
            for _ in range(20):
                jobs, trap.enqueued_jobs = list(trap.enqueued_jobs), []
                if not jobs:
                    break
                for job in jobs:
                    if not job.job_config.allow_commit:
                        job._env = self.env(user=job.env.uid, context=job.env.context)
                    job.perform()
                    self.env.flush_all()
                    self.env.invalidate_all()
        self.env.invalidate_all()

    def commands(self):
        found = []
        for path in sorted(glob.glob(os.path.join(self.spool, "cmd", "*.json"))):
            with open(path, encoding="utf-8") as handle:
                found.append(json.load(handle))
        return found

    def answer(self, session, text):
        last = self.env["flightdeck.message.text"].search(
            [("session_ref_id", "=", session.id)], order="seq desc", limit=1)
        start = (last.seq + 1) if last else 0
        prompt = session.reply_ids.filtered(lambda r: r.state == "waiting")[:1].prompt
        self.env["flightdeck.message.text"].create([
            {"uuid": "u-%s-%d" % (session.session_id, start + i), "session_ref_id": session.id,
             "seq": start + i, "role": role, "text": body}
            for i, (role, body) in enumerate([("user", prompt), ("assistant", text)])])
        self.runner._apply_event({"kind": "result", "session_id": session.session_id, "payload": {}})
        session.reply_ids.filtered(lambda r: r.state == "ended")._resume()

    def start(self, flow, trap):
        run = self.env["workflow.run"].browse(flow._execute_from_trigger("start", "schedule", {})["run_id"])
        self.drain(trap)
        return run

    # --- tests -----------------------------------------------------------------

    def test_ask_agent_waits_for_the_answer_and_goes_on(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "={{ _json['text'] }}",
                              "chat_key": "=test:{{ _json['chat'] }}", "session_name": "Test chat"})
            run = self.start(flow, trap)
            self.assertEqual(run.status, "waiting", run.error_message)
            session = self.env["flightdeck.session"].search([("chat_key", "=", "test:c-1")])
            self.assertEqual((session.name, session.runner_id), ("Test chat", self.runner))
            start, send = self.commands()
            self.assertEqual((start["op"], send["op"], send["args"]["text"]), ("start", "send", "What is 2 + 2?"))
            wait = self.env["workflow.run.wait"].search([("run_id", "=", run.id)])
            self.assertEqual(wait.correlation_key, session.reply_ids.key)

            self.answer(session, "4")
            self.drain(trap)
        self.assertEqual(run.status, "completed", run.error_message)
        self.assertEqual(run.output_data, {"said": "4", "chat": "c-1"})

    def test_answer_carries_the_session_and_key(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "={{ _json['text'] }}", "chat_key": "=test:{{ _json['chat'] }}"},
                             after="{'session_id': _json['session_id'], 'chat_key': _json['chat_key']}")
            run = self.start(flow, trap)
            session = self.env["flightdeck.session"].search([("chat_key", "=", "test:c-1")])
            self.answer(session, "4")
            self.drain(trap)
        self.assertEqual(run.output_data, {"session_id": session.session_id, "chat_key": "test:c-1"})

    def test_no_key_makes_one_session_for_the_run(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello"})
            run = self.start(flow, trap)
            key = "flow:%s:run:%s" % (flow.id, run.id)
            session = self.env["flightdeck.session"].search([("chat_key", "=", key)])
            self.assertEqual(session.name, flow.name)

    def test_no_answer_in_time_goes_out_on_timeout(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello", "chat_key": "=test:{{ _json['chat'] }}",
                              "timeout_amount": 1, "timeout_unit": "minutes"})
            run = self.start(flow, trap)
            wait = self.env["workflow.run.wait"].search([("run_id", "=", run.id)])
            self.assertTrue(wait.timeout_at)
            wait._resolve("timeout", {})
            self.drain(trap)
        self.assertEqual(run.status, "completed", run.error_message)
        self.assertEqual(run.output_data, {"late": True, "chat": "c-1"})

    def test_a_user_without_flightdeck_rights_cannot_ask(self):
        user = new_test_user(self.env, "agent_step_user",
                             groups="base.group_user,workflow_studio.group_workflow_manager")
        # The check runs before the send, on the flow's effective user.
        runner = AskAgentNodeRunner.__new__(AskAgentNodeRunner)
        runner.executor = type("Executor", (), {"_get_effective_execution_user": lambda _self: user})()
        with self.assertRaises(PermanentNodeError) as caught:
            runner._check_user()
        self.assertIn("FlightDeck manager", str(caught.exception))
        user.group_ids = [(4, self.env.ref("flight_deck.group_manager").id)]
        runner._check_user()
        self.assertFalse(self.commands(), "nothing was sent")

    def test_start_session_sends_and_goes_on(self):
        with trap_jobs() as trap:
            flow = self.env["ir.workflow"].create({"name": "Agent start %s" % self._testMethodName})
            flow.save_workflow(build([
                ("start", "schedule_trigger", {}),
                ("open", "fd_start_session", {"prompt": "Look at run 7", "chat_key": "test:start"}),
                ("next", "code", code("{'session_id': _json['session_id'], 'key': _json['chat_key']}")),
            ], [("start", "output", "open", "data"), ("open", "output", "next", "data")]))
            run = self.start(flow, trap)
        session = self.env["flightdeck.session"].search([("chat_key", "=", "test:start")])
        self.assertEqual(run.status, "completed", run.error_message)
        self.assertEqual(run.output_data, {"session_id": session.session_id, "key": "test:start"})
        self.assertEqual([c["op"] for c in self.commands()], ["start", "send"])

    def test_the_steps_count_as_waiting_and_outside(self):
        self.assertIn("fd_ask_agent", waiting_types())
        self.assertNotIn("fd_start_session", waiting_types())
        self.assertNotIn("call_flow", waiting_types())
        snapshot = {"nodes": [{"id": "a", "type": "fd_ask_agent", "config": {}},
                              {"id": "b", "type": "fd_start_session", "config": {}}]}
        self.assertEqual({n["id"] for n in atomic_violations(snapshot)}, {"a", "b"})
        flow = self.env["ir.workflow"].create({"name": "Agent atomic", "execution_style": "atomic"})
        with self.assertRaises(UserError):
            flow.save_workflow(build([("start", "schedule_trigger", {}), ("ask", "fd_ask_agent", {"prompt": "x"})],
                                     [("start", "output", "ask", "data")]))
