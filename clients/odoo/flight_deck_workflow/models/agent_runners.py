"""Workflow Studio steps over the chat bridge (flight_deck/models/chat_bridge.py).

Ask Agent sends a prompt to a FlightDeck session with ``fd_chat_send`` and
parks the run on the key it returns; the bridge resumes the run with the
session's answer once the runner reports the turn's end and the follow cron
has read the answer from the transcript. Start Session sends the prompt and
goes on without waiting.

The runners join Workflow Studio's runner table when this module is imported,
so the save checks see them as the built-in waiting and outside-reaching steps
(``parks_run``, ``reaches_outside``).
"""

import hashlib
import json

from odoo import _, fields
from odoo.exceptions import UserError, ValidationError

from odoo.addons.flight_deck.models.runner import EFFORTS, MODELS
from odoo.addons.workflow_engine.engine.context import build_eval_context, to_plain
from odoo.addons.workflow_engine.engine.registry import register_runner
from odoo.addons.workflow_engine.engine.runners.base import BaseNodeRunner
from odoo.addons.workflow_engine.engine.runners.errors import PermanentNodeError
from odoo.addons.workflow_engine.engine.runners.waiting import WAIT_RESUME, duration
from odoo.addons.workflow_studio.models.runners.wait_runners import WaitingStepMixin

MANAGER_GROUP = "flight_deck.group_manager"
DEFAULT_TIMEOUT = (10, "minutes")
# Each choice: the built-in tools the session has (None keeps them all) and the
# ones it may use without asking, on top of the runner's rules. Chat sessions
# run in dontAsk, where a tool no rule allows is refused.
WEB = ["WebSearch", "WebFetch"]
READ = ["Read", "Glob", "Grep"]
TOOL_SETS = {
    "": (None, []),
    "web": (WEB, WEB),
    "read": (READ, READ),
    "read_web": (READ + WEB, READ + WEB),
    "none": ([], []),
}
# The permission modes a step offers (Manual, Edit, Plan, Auto); dontAsk stays
# for the chat bridge's own callers.
STEP_MODES = ("default", "acceptEdits", "plan", "auto")
DEFAULT_MODE = "auto"
# Ask Agent's Go on: when the agent answers (the run waits), or right away.
GO_ON = ("answer", "sent")
# Without Instructions: the answer goes to the next step, not to a person in a chat.
STEP_PROMPT = (
    "You are one step of an automated workflow. Answer the request directly and completely; your whole "
    "reply is handed to the next step as text, so add no greeting or follow-up question."
)


class AgentStepMixin:
    """What both steps share: settings, the user check, the send."""

    reaches_outside = True

    def _eval(self, raw, eval_context, default=None):
        if raw in (None, ""):
            return default
        return to_plain(self.resolver.resolve(raw, eval_context))

    def _settings(self, node_config, input_data, context):
        eval_context = build_eval_context(input_data or {}, context, include_input_item=True)
        prompt = self._eval(node_config.get("prompt"), eval_context)
        if prompt in (None, "", False):
            raise PermanentNodeError("Ask Agent needs a prompt.", "validation")
        executor = self.executor
        key = self._eval(node_config.get("chat_key"), eval_context)
        if key in (None, "", False):
            # No key: the run is the conversation, so its agent steps share one session.
            workflow = executor._get_execution_workflow()
            key = "flow:%s:run:%s" % (workflow.id if workflow else 0, executor.run.id if executor.run else 0)
        name = self._eval(node_config.get("session_name"), eval_context)
        if name in (None, "", False):
            workflow = executor._get_execution_workflow()
            name = workflow.name if workflow else None
        # Empty is the runner's default, also on a session an earlier step set otherwise.
        model, effort = node_config.get("model") or "", node_config.get("effort") or ""
        if model and model not in dict(MODELS):
            raise PermanentNodeError("Unknown model %s." % model, "validation")
        if effort and effort not in dict(EFFORTS):
            raise PermanentNodeError("Unknown thinking effort %s." % effort, "validation")
        # A step saved before Mode existed runs as the panel shows it: Auto.
        mode = node_config.get("mode") or DEFAULT_MODE
        if mode not in STEP_MODES:
            raise PermanentNodeError("Unknown mode %s." % mode, "validation")
        instructions = self._eval(node_config.get("instructions"), eval_context)
        instructions = str(instructions) if instructions not in (None, "", False) else None
        options = start_options(node_config)
        if instructions or options:
            # These are fixed for a session's process: other ones need their own session.
            digest = hashlib.sha256(json.dumps([instructions, options], sort_keys=True).encode()).hexdigest()
            key = "%s:opt:%s" % (key, digest[:10])
        return {"prompt": str(prompt), "key": str(key), "name": str(name) if name else None,
                "model": model, "effort": effort, "mode": mode, "instructions": instructions, "options": options}

    def _check_user(self):
        user = self.executor._get_effective_execution_user()
        if not (user._is_superuser() or user.has_group(MANAGER_GROUP)):
            raise PermanentNodeError(
                "%s may not send to FlightDeck sessions: the flow's user needs the FlightDeck "
                "manager group." % user.display_name, "access")

    @staticmethod
    def _asked(s, session, now, timeout_at=None):
        """What the step asked and with what, read back from the session after the
        send: a step left to the runner's default gets the model the runner picked."""
        return {
            "chat_key": s["key"], "session_id": session.session_id,
            "model": dict(MODELS).get(session.live_model) or session.live_model or None,
            "effort": dict(EFFORTS).get(session.live_effort) or None,
            "asked_at": fields.Datetime.to_string(now),
            "timeout_at": fields.Datetime.to_string(timeout_at) if timeout_at else None,
        }

    def _send(self, s):
        """Send through the bridge; the reply key, and the session it went to."""
        self._check_user()
        Session = self.executor.env["flightdeck.session"].sudo()
        try:
            reply_key = Session.fd_chat_send(
                s["key"], s["prompt"], name=s["name"], model=s["model"], effort=s["effort"], mode=s["mode"],
                instructions=s["instructions"] or STEP_PROMPT, options=s["options"])
        except UserError as exc:
            raise PermanentNodeError(str(exc.args[0] if exc.args else exc), "connection") from exc
        session = Session.search([("chat_key", "=", s["key"])], limit=1)
        return reply_key, session

    @classmethod
    def validate_node(cls, node, snapshot):
        config = dict(node.get("config") or {})
        if not str(config.get("output_schema") or "").strip():
            # A schema not written yet is a step still being filled in; the run refuses it.
            config.pop("output_format", None)
        try:
            start_options(config)
        except PermanentNodeError as exc:
            raise ValidationError(_("Step %s: %s") % (node.get("label") or node.get("id"), exc.args[0])) from exc


def start_options(config):
    """The session's start options a step's settings ask for, in the runner's
    words (runner/start_options.go); empty settings give none."""
    options = {}
    if config.get("output_format") == "json":
        schema = config.get("output_schema")
        if isinstance(schema, str):
            try:
                schema = json.loads(schema) if schema.strip() else None
            except ValueError as exc:
                raise PermanentNodeError("The output schema is not JSON: %s" % exc, "validation") from exc
        if not isinstance(schema, dict) or not schema:
            raise PermanentNodeError("A JSON answer needs an output schema, as a JSON object.", "validation")
        options["json_schema"] = json.dumps(schema, sort_keys=True)
    tools = config.get("tools") or ""
    if tools not in TOOL_SETS:
        raise PermanentNodeError("Unknown tool set %s." % tools, "validation")
    available, allow = TOOL_SETS[tools]
    if available is not None:
        options["tools"] = available
    if allow:
        options["allow"] = allow
    budget = config.get("max_budget_usd")
    if budget not in (None, "", False, 0, "0"):
        try:
            budget = float(budget)
        except (TypeError, ValueError) as exc:
            raise PermanentNodeError("The cost limit must be a number of dollars.", "validation") from exc
        if budget < 0:
            raise PermanentNodeError("The cost limit cannot be negative.", "validation")
        if budget:
            options["max_budget_usd"] = budget
    turns = config.get("max_turns")
    if turns not in (None, "", False, 0, "0"):
        try:
            turns = int(turns)
        except (TypeError, ValueError) as exc:
            raise PermanentNodeError("Max turns must be a whole number.", "validation") from exc
        if turns < 0:
            raise PermanentNodeError("Max turns cannot be negative.", "validation")
        if turns:
            options["max_turns"] = turns
    return options


def step_output(status, asked, original, reply=None, answered_at=None):
    """What an agent step gives the next step, the same keys on every way out,
    so the next step's mapping holds whatever happened:

    status       sent | waiting | answered | timeout (not_sent: a preview, shown only)
    answer       the answer's text ("" until it is answered)
    data         the answer as JSON, with an output schema (else None)
    usage        cost, tokens, turns and time of the turn (None until answered,
                 or from a runner older than 2026-10-13)
    model, effort   what the session answered with, as named in the step's
                 settings ("Opus 5.5", "Medium"); the runner's default when the
                 step left it to the runner; effort None when no level is known
    session_id, chat_key   the FlightDeck session asked
    asked_at, answered_at, timeout_at   UTC, "YYYY-MM-DD HH:MM:SS" or None
    input        what the step received
    """
    reply = reply or {}
    return {
        "status": status,
        "answer": reply.get("text") or "",
        "data": reply.get("data"),
        "usage": reply.get("usage") or None,
        "model": asked.get("model"),
        "effort": asked.get("effort"),
        "session_id": reply.get("session_id") or asked.get("session_id"),
        "chat_key": asked.get("chat_key"),
        "asked_at": asked.get("asked_at"),
        "answered_at": answered_at,
        "timeout_at": asked.get("timeout_at"),
        "input": original,
    }


class AskAgentNodeRunner(AgentStepMixin, WaitingStepMixin):
    """Ask Agent: send a prompt to a FlightDeck session and, by default, wait for
    its answer. Go on "right away" sends and goes on at once. Outputs: ``answer``
    (labelled done: answered, or sent right away), ``timeout``, ``error``; each
    carries ``step_output``."""

    node_type = "fd_ask_agent"

    def execute(self, node_config, input_data, context):
        executor = self.executor
        node_id = context.get("current_node_id")
        if executor._current_socket == WAIT_RESUME:
            request = self._request(input_data)
            asked = (request or {}).get("_fd_ask") or {}
            original = (request or {}).get("input")
            if input_data.get("socket") == "timeout":
                return self._resume_result(node_id, "timeout", step_output("timeout", asked, original))
            reply = (input_data.get("payload") or {}).get("reply") or {}
            if reply.get("is_error"):
                # The session could not answer (sign-in, cost limit, API error): its text is the reason.
                raise PermanentNodeError("The agent did not answer (%s): %s" % (
                    reply.get("status") or "error", reply.get("text") or "no text"), "remote_refused")
            answered = step_output("answered", asked, original, reply,
                                   answered_at=fields.Datetime.to_string(fields.Datetime.now()))
            return self._resume_result(node_id, "answer", answered)

        s = self._settings(node_config, input_data, context)
        go_on = node_config.get("go_on") or "answer"
        if go_on not in GO_ON:
            raise PermanentNodeError("Unknown choice for Next step: %s." % go_on, "validation")
        if not executor.run:
            # A preview sends nothing and gives the next step nothing; its output shows
            # the keys a real run gives (and no other), for mapping the next step.
            return {"outputs": [[], [], []], "json": step_output("not_sent", {}, input_data)}
        now = fields.Datetime.now()
        if go_on == "sent":
            _reply_key, session = self._send(s)
            sent = step_output("sent", self._asked(s, session, now), input_data)
            return {"outputs": [[sent], [], []], "json": sent}
        Wait = executor.env["workflow.run.wait"]
        amount, unit = DEFAULT_TIMEOUT
        if node_config.get("timeout_amount") not in (None, "", 0, "0"):
            amount, unit = node_config.get("timeout_amount"), node_config.get("timeout_unit") or "minutes"
        timeout_at = Wait._limit_timeout(now + duration(amount, unit), now)
        reply_key, session = self._send(s)
        asked = self._asked(s, session, now, timeout_at)
        # The wait and the bridge's reply row commit together: the bridge resumes
        # only waits it can see, and the runner's result event comes after the commit.
        request = {"input": input_data, "_fd_ask": asked}
        wait = Wait._create_wait(executor, node_id, "event", "open", correlation_key=reply_key,
                                 timeout_at=timeout_at, request_payload=self._freeze(request))
        wait._schedule_timers()
        # What the run log shows while it waits: the same keys as the answer.
        return self._park(wait, dict(step_output("waiting", asked, input_data), waiting="agent"))


class StartSessionNodeRunner(AgentStepMixin, BaseNodeRunner):
    """Start Session: send a prompt to a FlightDeck session and go on without
    waiting, as Ask Agent with Go on "right away". Outputs: ``output``
    (``step_output`` with status sent), ``error``."""

    node_type = "fd_start_session"

    def execute(self, node_config, input_data, context):
        s = self._settings(node_config, input_data, context)
        if not self.executor.run:
            return {"outputs": [[], []], "json": step_output("not_sent", {}, input_data)}
        _reply_key, session = self._send(s)
        payload = step_output("sent", self._asked(s, session, fields.Datetime.now()), input_data)
        return {"outputs": [[payload], []], "json": payload}


register_runner(AskAgentNodeRunner.node_type, AskAgentNodeRunner)
register_runner(StartSessionNodeRunner.node_type, StartSessionNodeRunner)
