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

from odoo import fields
from odoo.exceptions import UserError

from odoo.addons.workflow_studio.models.context_objects import build_eval_context, to_plain
from odoo.addons.workflow_studio.models.runners.base import BaseNodeRunner
from odoo.addons.workflow_studio.models.runners.errors import PermanentNodeError
from odoo.addons.workflow_studio.models.runners.wait_runners import (
    WAIT_RESUME,
    WaitingStepMixin,
    duration,
)
from odoo.addons.workflow_studio.models.workflow_executor import WorkflowExecutor

MANAGER_GROUP = "flight_deck.group_manager"
DEFAULT_TIMEOUT = (10, "minutes")


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
        return str(prompt), str(key), (str(name) if name else None)

    def _check_user(self):
        user = self.executor._get_effective_execution_user()
        if not (user._is_superuser() or user.has_group(MANAGER_GROUP)):
            raise PermanentNodeError(
                "%s may not send to FlightDeck sessions: the flow's user needs the FlightDeck "
                "manager group." % user.display_name, "access")

    def _send(self, prompt, key, name):
        """Send through the bridge; the reply key, and the session it went to."""
        self._check_user()
        Session = self.executor.env["flightdeck.session"].sudo()
        try:
            reply_key = Session.fd_chat_send(key, prompt, name=name)
        except UserError as exc:
            raise PermanentNodeError(str(exc.args[0] if exc.args else exc), "connection") from exc
        session = Session.search([("chat_key", "=", key)], limit=1)
        return reply_key, session


class AskAgentNodeRunner(AgentStepMixin, WaitingStepMixin):
    """Ask Agent: send a prompt to a FlightDeck session and wait for its answer.
    Outputs: ``answer`` ({answer, session_id, chat_key, input}), ``timeout``
    ({input}), ``error``."""

    node_type = "fd_ask_agent"

    def execute(self, node_config, input_data, context):
        executor = self.executor
        node_id = context.get("current_node_id")
        if executor._current_socket == WAIT_RESUME:
            request = self._request(input_data)
            asked = (request or {}).get("_fd_ask") or {}
            original = (request or {}).get("input")
            if input_data.get("socket") == "timeout":
                return self._resume_result(node_id, "timeout", {"input": original, "chat_key": asked.get("chat_key")})
            reply = (input_data.get("payload") or {}).get("reply") or {}
            return self._resume_result(node_id, "answer", {
                "answer": reply.get("text") or "",
                "session_id": reply.get("session_id") or asked.get("session_id"),
                "chat_key": asked.get("chat_key"),
                "input": original,
            })

        prompt, key, name = self._settings(node_config, input_data, context)
        if not executor.run:
            # A preview without a run sends nothing.
            return {"outputs": [[], [], []], "json": {"would_ask": prompt, "chat_key": key}}
        Wait = executor.env["workflow.run.wait"]
        now = fields.Datetime.now()
        amount, unit = DEFAULT_TIMEOUT
        if node_config.get("timeout_amount") not in (None, "", 0, "0"):
            amount, unit = node_config.get("timeout_amount"), node_config.get("timeout_unit") or "minutes"
        timeout_at = Wait._limit_timeout(now + duration(amount, unit), now)
        reply_key, session = self._send(prompt, key, name)
        # The wait and the bridge's reply row commit together: the bridge resumes
        # only waits it can see, and it reads the answer minutes after the send.
        request = {"input": input_data, "_fd_ask": {"chat_key": key, "session_id": session.session_id}}
        wait = Wait._create_wait(executor, node_id, "event", "open", correlation_key=reply_key,
                                 timeout_at=timeout_at, request_payload=self._freeze(request))
        wait._schedule_timers()
        return self._park(wait, {"waiting": "agent", "chat_key": key, "session_id": session.session_id,
                                 "timeout_at": fields.Datetime.to_string(wait.timeout_at)})


class StartSessionNodeRunner(AgentStepMixin, BaseNodeRunner):
    """Start Session: send a prompt to a FlightDeck session and go on without
    waiting. Outputs: ``output`` ({session_id, chat_key, input}), ``error``."""

    node_type = "fd_start_session"

    def execute(self, node_config, input_data, context):
        prompt, key, name = self._settings(node_config, input_data, context)
        if not self.executor.run:
            return {"outputs": [[], []], "json": {"would_start": prompt, "chat_key": key}}
        _reply_key, session = self._send(prompt, key, name)
        payload = {"session_id": session.session_id, "chat_key": key, "input": input_data}
        return {"outputs": [[payload], []], "json": payload}


WorkflowExecutor.NODE_RUNNERS.update({
    AskAgentNodeRunner.node_type: AskAgentNodeRunner,
    StartSessionNodeRunner.node_type: StartSessionNodeRunner,
})
