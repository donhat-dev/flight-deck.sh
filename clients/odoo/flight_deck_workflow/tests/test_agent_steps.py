import glob
import json
import os
import tempfile

from odoo.exceptions import UserError, ValidationError
from odoo.tests import common, new_test_user, tagged

from odoo.addons.flight_deck.models.runner import CURRENT_MODELS, EFFORTS, MODELS
from odoo.addons.queue_job.tests.common import trap_jobs
from odoo.addons.workflow_studio.models.ir_workflow_orchestration import waiting_types
from odoo.addons.workflow_engine.engine.runners.errors import PermanentNodeError
from odoo.addons.workflow_engine.engine.runners.safety import atomic_violations

from ..models.agent_runners import STEP_PROMPT, TOOL_SETS, AskAgentNodeRunner
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

    def test_model_and_effort_start_the_session(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello", "model": "haiku", "effort": "low"})
            run = self.start(flow, trap)
            self.assertEqual(run.status, "waiting", run.error_message)
        start, _send = self.commands()
        self.assertEqual((start["args"]["model"], start["args"]["effort"]), ("haiku", "low"))

    def test_empty_model_and_effort_keep_the_runner_default(self):
        self.runner.write({"default_model": "sonnet", "default_effort": "high"})
        with trap_jobs() as trap:
            self.start(self.flow({"prompt": "Hello", "model": "", "effort": ""}), trap)
        start, _send = self.commands()
        self.assertEqual((start["args"]["model"], start["args"]["effort"]), ("sonnet", "high"))

    def test_an_unknown_model_fails_the_step(self):
        # The run fails before the send; the queue job reports it by raising.
        with trap_jobs() as trap, self.assertRaisesRegex(UserError, "Unknown model gpt"):
            self.start(self.flow({"prompt": "Hello", "model": "gpt"}), trap)
        self.assertFalse(self.commands(), "nothing was sent")

    def test_the_settings_offer_the_session_choices(self):
        # The step offers the current models only; the CLI aliases stay for older sessions.
        known = {"model": dict(CURRENT_MODELS), "effort": dict(EFFORTS)}
        for xmlid in ("workflow_type_fd_ask_agent", "workflow_type_fd_start_session"):
            schema = self.env.ref("flight_deck_workflow.%s" % xmlid).config_schema
            schema = json.loads(schema) if isinstance(schema, str) else schema
            self.assertNotIn("chat_key", schema)
            self.assertTrue(all(schema[k].get("section") == "options"
                                for k in ("instructions", "tools", "max_turns", "max_budget_usd")), xmlid)
            for field, labels in known.items():
                offered = {o["value"]: o["label"] for o in schema[field]["options"] if o["value"]}
                self.assertEqual(offered, labels, "%s %s" % (xmlid, field))
                self.assertEqual(schema[field]["options"][0]["value"], "", "empty comes first: the runner default")
            self.assertEqual({o["value"] for o in schema["tools"]["options"]}, set(TOOL_SETS))
            self.assertEqual(schema["tools"]["default"], "web")
        self.assertTrue(set(dict(CURRENT_MODELS)) <= set(dict(MODELS)))

    def test_web_search_is_allowed_and_the_step_prompt_is_set(self):
        with trap_jobs() as trap:
            self.start(self.flow({"prompt": "Weather in Hue?", "tools": "web"}), trap)
        start = self.commands()[0]
        self.assertEqual(start["args"]["tools"], ["WebSearch", "WebFetch"])
        self.assertTrue({"WebSearch", "WebFetch"} <= set(start["args"]["allowed_tools"]))
        self.assertEqual(start["args"]["append_system_prompt"], STEP_PROMPT)
        self.assertNotIn("no tools", start["args"]["append_system_prompt"])

    def result_event(self, session, **payload):
        self.runner._apply_event({"kind": "result", "session_id": session.session_id, "payload": dict({
            "result": "4", "is_error": False, "total_cost_usd": 0.002, "num_turns": 1, "duration_ms": 900,
            "usage": {"input_tokens": 3, "output_tokens": 20}}, **payload)})

    def test_the_result_event_answers_at_once_with_data_and_usage(self):
        schema = {"type": "object", "properties": {"sum": {"type": "integer"}}, "required": ["sum"]}
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello", "output_format": "json", "output_schema": json.dumps(schema)},
                             after="{'data': _json['data'], 'usage': _json['usage'], 'said': _json['answer']}")
            run = self.start(flow, trap)
            session = self.env["flightdeck.session"].search([("chat_key", "like", "flow:%:opt:%")])
            self.assertEqual(len(session), 1)
            self.result_event(session, result='{"sum":4}', structured_output={"sum": 4})
            self.drain(trap)
        self.assertEqual(run.status, "completed", run.error_message)
        self.assertEqual(run.output_data["data"], {"sum": 4})
        self.assertEqual(run.output_data["said"], '{"sum":4}')
        self.assertEqual((run.output_data["usage"]["cost_usd"], run.output_data["usage"]["output_tokens"]), (0.002, 20))
        self.assertEqual(json.loads(self.commands()[0]["args"]["json_schema"]), schema)

    def test_an_agent_error_is_not_an_answer(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello"})
            run = self.start(flow, trap)
            session = self.env["flightdeck.session"].search([("chat_key", "=", "flow:%s:run:%s" % (flow.id, run.id))])
            self.result_event(session, result="Failed to authenticate", is_error=True, subtype="success")
            with self.assertRaisesRegex(UserError, r"did not answer \(error\): Failed to authenticate"):
                self.drain(trap)

    def test_mode_starts_the_session_and_changes_it_later(self):
        with trap_jobs() as trap:
            flow = self.env["ir.workflow"].create({"name": "Agent modes"})
            flow.save_workflow(build([
                ("start", "schedule_trigger", {}),
                ("one", "fd_start_session", {"prompt": "one"}),
                ("two", "fd_start_session", {"prompt": "two", "mode": "plan"}),
                ("three", "fd_start_session", {"prompt": "three", "mode": "default"}),
            ], [("start", "output", "one", "data"), ("one", "output", "two", "data"),
                ("two", "output", "three", "data")]))
            run = self.start(flow, trap)
        self.assertEqual(run.status, "completed", run.error_message)
        ops = [(c["op"], c["args"].get("permission_mode") or c["args"].get("mode")) for c in self.commands()]
        # No mode saved: Auto, as the panel shows. Manual is the CLI's "default".
        self.assertEqual(ops, [("start", "auto"), ("send", None), ("set_mode", "plan"), ("send", None),
                               ("set_mode", "default"), ("send", None)])

    def test_an_unknown_mode_fails_the_step(self):
        with trap_jobs() as trap, self.assertRaisesRegex(UserError, "Unknown mode dontAsk"):
            self.start(self.flow({"prompt": "Hello", "mode": "dontAsk"}), trap)
        self.assertFalse(self.commands(), "nothing was sent")

    def test_the_settings_follow_the_asked_order_and_name_no_outside_service(self):
        schema = self.env.ref("flight_deck_workflow.workflow_type_fd_ask_agent").config_schema
        schema = json.loads(schema) if isinstance(schema, str) else schema
        self.assertEqual(list(schema)[:5], ["prompt", "mode", "model", "effort", "session_name"])
        self.assertEqual(schema["mode"]["default"], "auto")
        self.assertEqual([o["label"] for o in schema["mode"]["options"]], ["Manual", "Edit", "Plan", "Auto"])
        self.assertNotIn("zalo", json.dumps(schema).lower())

    def test_an_empty_model_goes_back_to_the_runner_default(self):
        self.runner.write({"default_model": "sonnet"})
        with trap_jobs() as trap:
            flow = self.env["ir.workflow"].create({"name": "Agent default"})
            flow.save_workflow(build([
                ("start", "schedule_trigger", {}),
                ("one", "fd_start_session", {"prompt": "one", "model": "haiku"}),
                ("two", "fd_start_session", {"prompt": "two", "model": ""}),
            ], [("start", "output", "one", "data"), ("one", "output", "two", "data")]))
            self.start(flow, trap)
        ops = [(c["op"], c["args"].get("model")) for c in self.commands()]
        self.assertEqual(ops, [("start", "haiku"), ("send", None), ("set_model", "sonnet"), ("send", None)])

    def test_the_effort_turn_is_not_the_answer(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello"}, after="{'said': _json['answer']}")
            run = self.start(flow, trap)
            session = self.env["flightdeck.session"].search([("chat_key", "=", "flow:%s:run:%s" % (flow.id, run.id))])
            self.result_event(session, result="Set effort level to low", command=True)
            self.assertEqual(session.reply_ids.state, "waiting")
            self.result_event(session, result="4")
            self.drain(trap)
        self.assertEqual(run.output_data, {"said": "4"})

    def test_options_start_their_own_session(self):
        with trap_jobs() as trap:
            flow = self.env["ir.workflow"].create({"name": "Agent options"})
            flow.save_workflow(build([
                ("start", "schedule_trigger", {}),
                ("plain", "fd_start_session", {"prompt": "one"}),
                ("narrow", "fd_start_session", {"prompt": "two", "instructions": "Be brief.", "tools": "read",
                                                "max_budget_usd": 0.5, "max_turns": 5}),
            ], [("start", "output", "plain", "data"), ("plain", "output", "narrow", "data")]))
            run = self.start(flow, trap)
        self.assertEqual(run.status, "completed", run.error_message)
        starts = [c for c in self.commands() if c["op"] == "start"]
        self.assertEqual(len(starts), 2, "different start options: two sessions")
        plain, narrow = starts
        self.assertNotIn("tools", plain["args"])
        self.assertEqual((narrow["args"]["tools"], narrow["args"]["max_budget_usd"], narrow["args"]["max_turns"]),
                         (["Read", "Glob", "Grep"], 0.5, 5))
        self.assertEqual(narrow["args"]["append_system_prompt"], "Be brief.")
        self.assertEqual(narrow["args"]["name"], "Agent options")

    def test_a_broken_schema_cannot_be_saved(self):
        flow = self.env["ir.workflow"].create({"name": "Agent schema"})
        with self.assertRaisesRegex(ValidationError, "not JSON"):
            flow.save_workflow(build([
                ("start", "schedule_trigger", {}),
                ("ask", "fd_ask_agent", {"prompt": "x", "output_format": "json", "output_schema": "{oops"}),
            ], [("start", "output", "ask", "data")]))
        # A schema not written yet is a step still being filled in.
        flow.save_workflow(build([
            ("start", "schedule_trigger", {}),
            ("ask", "fd_ask_agent", {"prompt": "x", "output_format": "json"}),
        ], [("start", "output", "ask", "data")]))

    def test_every_way_out_gives_the_same_fields(self):
        keys = "{k: _json[k] for k in ('status', 'answer', 'data', 'usage', 'session_id', 'chat_key', 'input')}"
        stamps = "{k: bool(_json[k]) for k in ('asked_at', 'answered_at', 'timeout_at')}"
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello", "chat_key": "test:shape"},
                             after="dict(%s, stamps=%s)" % (keys, stamps))
            run = self.start(flow, trap)
            parked = run.node_run_ids.filtered(lambda r: r.node_id == "ask")
            self.assertEqual((parked.output_data["status"], parked.output_data["waiting"]), ("waiting", "agent"))
            session = self.env["flightdeck.session"].search([("chat_key", "=", "test:shape")])
            self.result_event(session, result="4")
            self.drain(trap)
        self.assertEqual(run.output_data, {
            "status": "answered", "answer": "4", "data": None, "usage": run.output_data["usage"],
            "session_id": session.session_id, "chat_key": "test:shape",
            "input": {"chat": "c-1", "text": "What is 2 + 2?"},
            "stamps": {"asked_at": True, "answered_at": True, "timeout_at": True}})

    def test_the_output_names_the_model_and_effort(self):
        with trap_jobs() as trap:
            flow = self.env["ir.workflow"].create({"name": "Agent model %s" % self._testMethodName})
            flow.save_workflow(build([
                ("start", "schedule_trigger", {}),
                ("picked", "fd_ask_agent", {"prompt": "one", "go_on": "sent", "chat_key": "test:m1",
                                            "model": "claude-opus-5-5", "effort": "medium"}),
                ("runner", "fd_ask_agent", {"prompt": "two", "go_on": "sent", "chat_key": "test:m2"}),
                # The second step's input is the first step's output.
                ("out", "code", code("{'picked': _json['input']['model'] + ' ' + _json['input']['effort'], "
                                     "'runner': _json['model']}")),
            ], [("start", "output", "picked", "data"), ("picked", "answer", "runner", "data"),
                ("runner", "answer", "out", "data")]))
            run = self.start(flow, trap)
        self.assertEqual(run.status, "completed", run.error_message)
        # Left to the runner: the runner's default model (Sonnet 5.5 unless changed).
        self.assertEqual(run.output_data, {"picked": "Opus 5.5 Medium", "runner": "Sonnet 5.5"})

    def test_go_on_right_away_sends_and_does_not_wait(self):
        with trap_jobs() as trap:
            flow = self.flow({"prompt": "Hello", "chat_key": "test:away", "go_on": "sent"},
                             after="{'status': _json['status'], 'answer': _json['answer'], 'key': _json['chat_key']}")
            run = self.start(flow, trap)
        self.assertEqual(run.status, "completed", run.error_message)
        self.assertEqual(run.output_data, {"status": "sent", "answer": "", "key": "test:away"})
        self.assertFalse(self.env["workflow.run.wait"].search([("run_id", "=", run.id)]))
        self.assertEqual([c["op"] for c in self.commands()], ["start", "send"])

    def test_go_on_comes_after_the_session_and_the_main_way_out_reads_done(self):
        record = self.env.ref("flight_deck_workflow.workflow_type_fd_ask_agent")
        schema = json.loads(record.config_schema) if isinstance(record.config_schema, str) else record.config_schema
        self.assertEqual(list(schema)[:6], ["prompt", "mode", "model", "effort", "session_name", "go_on"])
        self.assertEqual(schema["timeout_amount"]["visibleWhen"], {"go_on": ["answer"]})
        sockets = json.loads(record.output_schema) if isinstance(record.output_schema, str) else record.output_schema
        self.assertEqual(sockets["answer"]["label"], "done")

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
