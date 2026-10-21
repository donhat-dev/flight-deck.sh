"""Tool calls per turn: the deterministic part of the turn analysis.

Layer 0 makes a turn readable: a turn with no recorded end ends where the next
one starts, and the answering model is taken from its own lines. Layer 1
stores one row per tool call, subagents included, with the time from call to
result. Layer 2 sorts each failed call into a family (`step_reader`).

These rows are the ruler across model releases: the rules that made a row
are on the row, and turns read under older rules are read again.
"""
import logging
import os
from datetime import timedelta

from odoo import api, fields, models

from . import step_reader

_logger = logging.getLogger(__name__)

STEPS_STATES = [
    ("todo", "Not read"),
    ("done", "Read"),
    ("partial", "Read, background agents may still run"),
    ("no_file", "Transcript missing"),
    ("bad_range", "Range unusable"),
]
# A background agent started in a turn can outlive it; its calls are read again
# once the turn has been over this long.
SETTLE = timedelta(minutes=30)
BATCH = 150


class FlightdeckTurnStep(models.Model):
    _name = "flightdeck.turn.step"
    _description = "Tool call in a turn"
    _order = "turn_id desc, sequence"
    _rec_name = "name"

    turn_id = fields.Many2one("flightdeck.turn", "Turn", required=True, ondelete="cascade", index=True)
    session_ref_id = fields.Many2one("flightdeck.session", "Session", index=True, ondelete="cascade")
    model = fields.Char(related="turn_id.turn_model", store=True, index=True)
    turn_started_at = fields.Datetime(related="turn_id.started_at", store=True, string="Turn start")
    sequence = fields.Integer()
    agent_key = fields.Char("Agent", index=True, help="Empty for the main agent, else the subagent's id.")
    agent_ref_id = fields.Many2one("flightdeck.session.agent", "Subagent", ondelete="set null")
    tool_use_id = fields.Char(index=True)
    name = fields.Char("Tool", index=True)
    server = fields.Char("MCP server", index=True)
    input_head = fields.Char("Input")
    input_hash = fields.Char(index=True)
    started_at = fields.Datetime()
    ended_at = fields.Datetime()
    duration = fields.Float("Seconds", digits=(10, 1), aggregator="sum")
    outcome = fields.Selection([("ok", "Done"), ("error", "Error"), ("no_result", "No result")],
                               index=True)
    family = fields.Selection([(f, f.capitalize()) for f in step_reader.FAMILIES], index=True,
                              help="Whose failure it was: avoidable (the agent's own), failed (a command "
                                   "that ran and failed), guard (a hook, the classifier or a person said "
                                   "no), environment, expected (nothing matched), person (interrupted).")
    error_class = fields.Char("Error kind", index=True)
    error_head = fields.Char("Error")
    repeat = fields.Boolean("Exact repeat", help="Same tool, same whole input, same agent, earlier in the turn.")
    rules_version = fields.Integer()


class FlightdeckTurnSteps(models.Model):
    _inherit = "flightdeck.turn"

    step_ids = fields.One2many("flightdeck.turn.step", "turn_id", "Tool call rows")
    steps_state = fields.Selection(STEPS_STATES, default="todo", index=True, readonly=True)
    # Not `model`: that one is the task's, set by the rollup on task rows only.
    turn_model = fields.Char("Answered by", readonly=True, index=True,
                             help="The main agent's model on most of this turn's own lines.")
    steps_version = fields.Integer(readonly=True)
    steps_note = fields.Char(readonly=True)
    tool_calls = fields.Integer("Tool calls", readonly=True, aggregator="sum")
    sub_tool_calls = fields.Integer("By subagents", readonly=True, aggregator="sum")
    subagents_read = fields.Integer("Subagents read", readonly=True)
    avoidable_errors = fields.Integer("Avoidable errors", readonly=True, aggregator="sum")
    failed_calls = fields.Integer("Failed commands", readonly=True, aggregator="sum")
    guard_hits = fields.Integer("Blocked", readonly=True, aggregator="sum")
    environment_errors = fields.Integer("Environment errors", readonly=True, aggregator="sum")
    exact_repeats = fields.Integer("Exact repeats", readonly=True, aggregator="sum")
    max_repeat = fields.Integer("Most repeats of one call", readonly=True, aggregator="max")
    tool_seconds = fields.Float("Tool seconds", readonly=True, aggregator="sum")
    interruptions = fields.Integer(readonly=True, aggregator="sum")

    # --- layer 0 ----------------------------------------------------------------------
    def _step_range(self):
        """(start, end) of this turn's own lines; end 0 means the end of the file.

        A turn whose end was not recorded (a resumed copy hid the next prompt
        from the reader) ends where the next turn of the same file starts; the
        end is written back so every reader of the turn agrees.
        """
        self.ensure_one()
        start, end = self.byte_start or 0, self.byte_end or 0
        if end:
            return (start, end) if end > start else None
        later = self.search([("session_ref_id", "=", self.session_ref_id.id),
                             ("byte_start", ">", start)], order="byte_start", limit=1)
        if later:
            self.byte_end = later.byte_start
            self.steps_note = "End taken from the next turn's start."
            return start, later.byte_start
        return start, 0

    # --- layers 1 and 2 ---------------------------------------------------------------
    def _read_steps(self):
        """Read the tool calls of these turns and store them with their families."""
        Step = self.env["flightdeck.turn.step"]
        Agent = self.env["flightdeck.session.agent"]
        now = fields.Datetime.now()
        for turn in self:
            path = turn.session_ref_id.follow_path
            if not path or not os.path.exists(path):
                turn.write({"steps_state": "no_file", "steps_version": step_reader.RULES_VERSION})
                continue
            span = turn._step_range()
            if span is None:
                turn.write({"steps_state": "bad_range", "steps_version": step_reader.RULES_VERSION})
                continue
            try:
                rows = step_reader.read_rows(path, *span)
            except OSError as e:
                turn.write({"steps_state": "no_file", "steps_note": str(e)[:200]})
                continue
            subdir = path[:-len(".jsonl")] + "/subagents"
            steps, models, interrupts, agents_read = step_reader.with_subagents(rows, subdir)
            nums = step_reader.summarize(steps)
            agent_rows = {a.agent_id: a.id for a in Agent.search([
                ("session_ref_id", "=", turn.session_ref_id.id),
                ("agent_id", "in", list({s["agent_key"] for s in steps if s["agent_key"]}))])}
            turn.step_ids.unlink()
            Step.create([{
                "turn_id": turn.id,
                "session_ref_id": turn.session_ref_id.id,
                "sequence": i,
                "agent_key": s["agent_key"] or False,
                "agent_ref_id": agent_rows.get(s["agent_key"]) or False,
                "tool_use_id": s["tool_use_id"],
                "name": s["name"],
                "server": s["server"] or False,
                "input_head": step_reader.one_line(s["input"], 200),
                "input_hash": s["input_hash"],
                "started_at": s["started"] and s["started"].replace(tzinfo=None),
                "ended_at": s["ended"] and s["ended"].replace(tzinfo=None),
                "duration": s["duration"],
                "outcome": s["outcome"],
                "family": s["family"] or False,
                "error_class": s["error_class"] or False,
                "error_head": step_reader.one_line(s["error_text"], 300) or False,
                "repeat": s["repeat"],
                "rules_version": step_reader.RULES_VERSION,
            } for i, s in enumerate(steps)])
            background = any(s["name"] in step_reader.AGENT_TOOLS and s.get("child_async") for s in steps)
            settled = turn.ended_at and turn.ended_at < now - SETTLE
            vals = {
                "steps_state": "partial" if background and not settled else "done",
                "steps_version": step_reader.RULES_VERSION,
                "tool_calls": nums["calls"],
                "sub_tool_calls": nums["sub_calls"],
                "subagents_read": agents_read,
                "avoidable_errors": nums["avoidable"],
                "failed_calls": nums["failed"],
                "guard_hits": nums["guard"],
                "environment_errors": nums["environment"],
                "exact_repeats": nums["repeats"],
                "max_repeat": nums["max_repeat"],
                "tool_seconds": nums["tool_seconds"],
                "interruptions": interrupts,
            }
            vals["turn_model"] = models.most_common(1)[0][0] if models else False
            turn.write(vals)

    @api.model
    def _cron_read_steps(self, limit=BATCH):
        """Read turns that ended and were never read, were read under older rules,
        or were read while a background agent could still run."""
        settled = fields.Datetime.now() - SETTLE
        todo = self.search([
            ("ended_at", "!=", False), "|", "|",
            ("steps_state", "=", "todo"),
            ("steps_version", "<", step_reader.RULES_VERSION),
            "&", ("steps_state", "=", "partial"), ("ended_at", "<", settled),
        ], order="ended_at desc", limit=limit)
        for turn in todo:
            try:
                turn._read_steps()
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception("reading the tool calls of turn %s failed", turn.id)
        return len(todo)

    def action_read_steps(self):
        self._read_steps()
        return True

    # --- layer 3's input ----------------------------------------------------------------
    def _analysis_digest(self):
        """The bounded, redacted text a reviewer reads for this turn, or False."""
        self.ensure_one()
        path = self.session_ref_id.follow_path
        span = path and os.path.exists(path) and self._step_range()
        if not span:
            return False
        steps, _models, _interrupts, _agents = step_reader.with_subagents(
            step_reader.read_rows(path, *span), path[:-len(".jsonl")] + "/subagents")
        after = self.search([("session_ref_id", "=", self.session_ref_id.id),
                             ("started_at", ">", self.started_at)], order="started_at", limit=1)
        task = self.task_id if self.task_id and self.task_id != self else False
        meta = {
            "id": self.id,
            "model": self.turn_model,
            "intent": self.intent,
            "task": task and (task.title or task.prompt_head),
            "prompt": self.prompt_text or self.prompt_head,
            "hours": self.own_hours,
        }
        next_turn = after and {"intent": after.intent, "feedback": after.feedback_ids.mapped("code"),
                               "prompt": after.prompt_text or after.prompt_head}
        return step_reader.digest(meta, steps, self.final_head, next_turn)
