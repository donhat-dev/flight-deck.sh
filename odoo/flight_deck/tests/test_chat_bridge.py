import glob
import json
import os
import tempfile

from odoo.tests import common, tagged

from odoo.addons.queue_job.tests.common import trap_jobs
from odoo.addons.workflow_studio.tests.test_join import build, code


@tagged("post_install", "-at_install")
class TestChatBridge(common.TransactionCase):
    """A flow asks a session and waits; the session's answer resumes it."""

    def setUp(self):
        super().setUp()
        self.env["workflow.run.wait"].search([("state", "in", ("open", "reserved"))]).write({"state": "cancelled"})
        self.spool = tempfile.mkdtemp(prefix="fd-chat-")
        self.runner = self.env["flightdeck.runner"].create({
            "name": "Chat test runner", "kind": "spool", "zone": "container", "spool_dir": self.spool,
            "default_cwd": "/tmp/fd-chat-home", "permission_mode": "dontAsk"})

    def flow(self):
        ask = ("result = {'key': env['flightdeck.session'].fd_chat_send("
               "'test:' + _json['chat'], _json['text'], runner_id=%d), 'chat': _json['chat']}" % self.runner.id)
        flow = self.env["ir.workflow"].create({"name": "Chat %s" % self._testMethodName})
        flow.save_workflow(build([
            ("start", "schedule_trigger", {}),
            ("input", "code", code("{'chat': 'c-1', 'text': 'What is 2 + 2?'}")),
            ("ask", "code", {"code": ask}),
            ("answer", "wait", {"mode": "event", "correlation_key": "={{ _json['key'] }}",
                                "timeout_amount": 10, "timeout_unit": "minutes"}),
            ("reply", "code", code("{'chat': _json['input']['chat'], 'text': _json['reply']['text']}")),
        ], [("start", "output", "input", "data"), ("input", "output", "ask", "data"),
            ("ask", "output", "answer", "data"), ("answer", "resumed", "reply", "data")]))
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

    def transcript(self, session, *lines):
        self.env["flightdeck.message.text"].create([
            {"uuid": "u-%s-%d" % (session.session_id, seq), "session_ref_id": session.id, "seq": seq,
             "role": role, "text": text}
            for seq, (role, text) in enumerate(lines)])

    def test_the_answer_of_the_turn_resumes_the_flow(self):
        with trap_jobs() as trap:
            flow = self.flow()
            run = self.env["workflow.run"].browse(flow._execute_from_trigger("start", "schedule", {})["run_id"])
            self.drain(trap)
            self.assertEqual(run.status, "waiting")

            session = self.env["flightdeck.session"].search([("chat_key", "=", "test:c-1")])
            self.assertEqual((session.runner_id, session.live_permission_mode), (self.runner, "dontAsk"))
            start, send = self.commands()
            self.assertEqual((start["op"], send["op"]), ("start", "send"))
            self.assertIn("plain text", start["args"]["append_system_prompt"])
            self.assertEqual(send["args"]["text"], "What is 2 + 2?")
            reply = session.reply_ids
            self.assertEqual(reply.state, "waiting")

            # The runner says the turn is over before the cron has read its text.
            self.transcript(session, ("user", "What is 2 + 2?"))
            self.runner._apply_event({"kind": "result", "session_id": session.session_id, "payload": {}})
            self.assertEqual(reply.state, "ended")
            reply._resume()
            self.assertEqual(reply.state, "ended", "no answer read yet: nothing resumes")
            self.assertEqual(run.status, "waiting")

            self.transcript_more(session, ("assistant", "4"))
            reply._resume()
            self.assertEqual((reply.state, reply.reply), ("answered", "4"))
            self.drain(trap)
        self.assertEqual(run.status, "completed", run.error_message)
        self.assertEqual(run.output_data, {"chat": "c-1", "text": "4"})

    def transcript_more(self, session, *lines):
        last = self.env["flightdeck.message.text"].search([("session_ref_id", "=", session.id)], order="seq desc",
                                                          limit=1)
        start = (last.seq + 1) if last else 0
        self.env["flightdeck.message.text"].create([
            {"uuid": "u-%s-%d" % (session.session_id, start + i), "session_ref_id": session.id, "seq": start + i,
             "role": role, "text": text}
            for i, (role, text) in enumerate(lines)])

    def test_each_answer_goes_to_its_own_turn(self):
        Session = self.env["flightdeck.session"]
        first = Session.fd_chat_send("test:c-2", "first question", runner_id=self.runner.id)
        session = Session.search([("chat_key", "=", "test:c-2")])
        self.transcript(session, ("user", "first question"), ("assistant", "first answer"))
        second = Session.fd_chat_send("test:c-2", "second question", runner_id=self.runner.id)
        self.assertEqual(Session.search_count([("chat_key", "=", "test:c-2")]), 1, "one session per chat")
        self.transcript_more(session, ("user", "second question"), ("assistant", "second answer"))
        for _turn in range(2):
            self.runner._apply_event({"kind": "result", "session_id": session.session_id, "payload": {}})
        session.reply_ids._resume()
        answers = {r.key: r.reply for r in session.reply_ids}
        self.assertEqual(answers, {first: "first answer", second: "second answer"})

    def test_a_tool_call_is_not_part_of_the_answer(self):
        Session = self.env["flightdeck.session"]
        Session.fd_chat_send("test:c-3", "q", runner_id=self.runner.id)
        session = Session.search([("chat_key", "=", "test:c-3")])
        self.transcript(session, ("user", "q"), ("assistant", "Let me look."), ("assistant", "done"))
        self.env["flightdeck.message.text"].search(
            [("session_ref_id", "=", session.id), ("text", "=", "Let me look.")]).tool_name = "Read"
        self.runner._apply_event({"kind": "result", "session_id": session.session_id, "payload": {}})
        session.reply_ids._resume()
        self.assertEqual(session.reply_ids.reply, "done")
